"""Shared commands for dev.sh and dev.ps1. Uses only the Python standard library."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "setup-ssh-cli"
UV_INSTALL = "https://docs.astral.sh/uv/getting-started/installation/"
COMMANDS = {
    "help": "Show these commands.",
    "doctor": "Check Python, uv, SSH tools, and the installed command.",
    "install": "Install this checkout as a standalone user command with uv.",
    "reinstall": "Rebuild and refresh that installation after code changes.",
    "uninstall": "Remove the uv-installed command; keep SSH files and saved devices.",
    "develop": "Create/update .venv with the locked development dependencies.",
    "run": "Run setup-ssh from this checkout; forward all remaining arguments.",
    "check": "Check lint and formatting without rewriting source files.",
    "fmt": "Format the Python code.",
    "test": "Run pytest; forward all remaining arguments.",
    "build": "Build a wheel and source archive in dist/.",
}


def help_text() -> str:
    commands = "\n".join(f"  {name:12} {description}" for name, description in COMMANDS.items())
    return (
        "Usage: ./dev.sh <command> [args]\n"
        "   or: .\\dev.ps1 <command> [args]\n\n"
        f"{commands}\n\n"
        "Requires Python 3.11+ and uv. With no arguments, the helper only shows help.\n"
        "Examples: ./dev.sh run --help | ./dev.sh test -q | ./dev.sh reinstall\n"
        "See docs/development.md for the details."
    )


def invoke(arguments: list[str], *, cwd: Path = ROOT) -> int:
    result = subprocess.run(arguments, cwd=cwd, check=False)
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


def doctor(uv: str | None) -> int:
    print(f"Checkout: {ROOT}")
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    print(f"Lockfile: {'found' if (ROOT / 'uv.lock').is_file() else 'MISSING'}")
    print(f"Project environment: {'found' if (ROOT / '.venv').is_dir() else 'not created yet'}")
    print(f"uv: {uv or 'MISSING'}", flush=True)
    status = invoke([uv, "--version"]) if uv else 1
    if not uv:
        print(f"Install uv: {UV_INSTALL}")
    for name in ("ssh", "ssh-keygen"):
        location = shutil.which(name)
        print(f"{name}: {location or 'MISSING (needed for local SSH setup)'}")
        if not location:
            status = status or 1
    print(f"netbird (optional): {shutil.which('netbird') or 'not found'}")
    print(f"setup-ssh on PATH: {shutil.which('setup-ssh') or 'not installed or not on PATH'}")
    if not (ROOT / "uv.lock").is_file():
        status = status or 1
    return status


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args.pop(0) if args else "help"
    if command in ("-h", "--help"):
        command = "help"
    if command not in COMMANDS:
        print(
            f"Unknown command: {command}. Run ./dev.sh help (or .\\dev.ps1 help).", file=sys.stderr
        )
        return 2
    if command not in ("run", "test") and args:
        if args == ["--help"] or args == ["-h"]:
            print(f"{command}: {COMMANDS[command]}")
            return 0
        print(f"{command} doesn't accept arguments. See docs/development.md.", file=sys.stderr)
        return 2
    if command == "help":
        print(help_text())
        return 0

    uv = shutil.which("uv")
    if command == "doctor":
        return doctor(uv)
    if not uv:
        print(f"uv is required for '{command}'. Install it: {UV_INSTALL}", file=sys.stderr)
        return 1

    if command in ("install", "reinstall"):
        install = [uv, "tool", "install", "--python", sys.executable]
        if command == "reinstall":
            install += ["--reinstall-package", PACKAGE]
        status = invoke(install + [str(ROOT)])
        if status == 0:
            print("Installed. Use setup-ssh --help to get started.")
            print("After editing the code, run the dev helper's reinstall command to update it.")
        return status
    if command == "uninstall":
        return invoke([uv, "tool", "uninstall", PACKAGE])
    if command == "develop":
        return invoke([uv, "sync", "--locked", "--group", "dev"])
    if command == "build":
        return invoke([uv, "build", "--out-dir", str(ROOT / "dist"), str(ROOT)])

    run = [uv, "run", "--project", str(ROOT), "--locked", "--group", "dev", "--"]
    if command == "run":
        # Relative --config paths belong to the caller, even outside the checkout.
        return invoke(run + ["python", "-m", "setup_ssh", *args], cwd=Path.cwd())
    if command == "test":
        return invoke(run + ["python", "-m", "pytest", *args])
    if command == "fmt":
        return invoke(run + ["ruff", "format", "."])
    if command == "check":
        lint = invoke(run + ["ruff", "check", "."])
        formatting = invoke(run + ["ruff", "format", "--check", "."])
        return lint or formatting
    raise AssertionError(f"Unhandled command: {command}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except OSError as error:
        print(f"dev: {error}", file=sys.stderr)
        sys.exit(1)
