"""Exercise the launchers and command boundaries without touching a user's tools."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = os.environ.get("SETUP_SSH_TEST_POWERSHELL") or shutil.which("pwsh")


@pytest.fixture
def dev():
    spec = importlib.util.spec_from_file_location("dev_helper", ROOT / "scripts" / "dev.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("launcher", ["sh", "ps1"])
def test_launcher_preserves_arguments_cwd_and_exit_status(tmp_path, launcher):
    if launcher == "sh" and os.name == "nt":
        pytest.skip("POSIX launcher")
    if launcher == "ps1" and not PWSH:
        pytest.skip("PowerShell is not installed")
    checkout = tmp_path / "checkout with spaces café"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / f"dev.{launcher}", checkout)
    # A tiny stand-in isolates the launcher's quoting and exit handling from uv.
    (checkout / "scripts" / "dev.py").write_text(
        "import json, os, sys\n"
        "print(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd()}))\n"
        "sys.exit(23)\n",
        encoding="utf-8",
    )
    caller = tmp_path / "caller"
    caller.mkdir()
    arguments = [
        "run",
        "--config",
        'folder with spaces/a "quoted" file.json',
        "",
        "café",
        "trailing slash\\",
        'backslash before quote\\"',
        "$(not-a-command); & | *",
        "--help",
    ]
    prefix = ["sh"] if launcher == "sh" else [PWSH, "-NoLogo", "-NoProfile", "-File"]
    result = subprocess.run(
        [*prefix, str(checkout / f"dev.{launcher}"), *arguments],
        cwd=caller,
        env={**os.environ, "SETUP_SSH_PYTHON": sys.executable},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 23, result.stderr + result.stdout
    assert json.loads(result.stdout) == {"args": arguments, "cwd": str(caller)}


def test_help_and_invalid_commands_need_no_uv(dev, monkeypatch, capsys):
    def unexpected_lookup(_):
        pytest.fail("Help and usage errors must not need external tools")

    monkeypatch.setattr(dev.shutil, "which", unexpected_lookup)
    assert dev.main([]) == 0
    assert "reinstall" in capsys.readouterr().out
    assert dev.main(["unknown"]) == 2
    assert "Unknown command" in capsys.readouterr().err
    assert dev.main(["install", "--force"]) == 2
    assert "doesn't accept arguments" in capsys.readouterr().err


def test_missing_uv_is_actionable(dev, monkeypatch, capsys):
    monkeypatch.setattr(dev.shutil, "which", lambda _: None)
    assert dev.main(["install"]) == 1
    assert "Install it: https://docs.astral.sh/uv/" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["run", "test"])
def test_forwarded_commands_keep_the_right_working_directory(dev, monkeypatch, tmp_path, command):
    calls = []
    monkeypatch.setattr(dev.shutil, "which", lambda _: "/tools/uv")
    monkeypatch.chdir(tmp_path)

    def invoke(arguments, *, cwd=dev.ROOT):
        calls.append((arguments, cwd))
        return 17

    monkeypatch.setattr(dev, "invoke", invoke)
    arguments = ["--help", "with spaces", "", '"quotes"']
    assert dev.main([command, *arguments]) == 17
    invoked, cwd = calls.pop()
    assert invoked[-len(arguments) :] == arguments
    assert "--locked" in invoked
    assert cwd == (tmp_path if command == "run" else dev.ROOT)


@pytest.mark.parametrize("failures", [(7, 0), (0, 9), (0, 0)])
def test_check_reports_lint_or_format_failure_without_fixing(dev, monkeypatch, failures):
    calls = []
    codes = iter(failures)
    monkeypatch.setattr(dev.shutil, "which", lambda _: "/tools/uv")

    def invoke(arguments):
        calls.append(arguments)
        return next(codes)

    monkeypatch.setattr(dev, "invoke", invoke)
    assert dev.main(["check"]) == (failures[0] or failures[1])
    assert len(calls) == 2
    assert not any("--fix" in call for call in calls)
    assert calls[1][-4:] == ["ruff", "format", "--check", "."]


def test_doctor_only_inspects_tools(dev, monkeypatch, tmp_path, capsys):
    (tmp_path / "uv.lock").write_text("placeholder\n")
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev.shutil, "which", lambda name: f"/tools/{name}")
    calls = []
    monkeypatch.setattr(dev, "invoke", lambda arguments: calls.append(arguments) or 0)
    assert dev.main(["doctor"]) == 0
    assert calls == [["/tools/uv", "--version"]]
    assert list(tmp_path.iterdir()) == [tmp_path / "uv.lock"]
    assert "setup-ssh on PATH: /tools/setup-ssh" in capsys.readouterr().out
