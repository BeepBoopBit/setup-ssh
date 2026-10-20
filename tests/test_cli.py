import json

import pytest

from setup_ssh.cli import main


def test_batch_enroll_and_offline_plan(tmp_path, capsys):
    path = tmp_path / "inventory.json"
    prefix = ["--config", str(path), "--batch"]
    for name, host in [("linux", "192.168.1.2"), ("windows", "100.100.1.3")]:
        assert (
            main(
                prefix
                + [
                    "add",
                    "--name",
                    name,
                    "--host",
                    host,
                    "--user",
                    "alex",
                    "--no-sync",
                ]
            )
            == 0
        )
    before = path.read_bytes()
    assert main(prefix + ["sync", "--dry-run"]) == 0
    assert path.read_bytes() == before
    assert "2 connection directions" in capsys.readouterr().out
    assert len(json.loads(before)["devices"]) == 2


def test_batch_missing_fields_and_invalid_port(tmp_path, capsys):
    path = tmp_path / "inventory.json"
    assert main(["--config", str(path), "--batch", "add"]) == 1
    assert not path.exists()
    assert (
        main(
            [
                "--config",
                str(path),
                "--batch",
                "add",
                "--name",
                "bad-port",
                "--host",
                "192.168.1.2",
                "--user",
                "alex",
                "--port",
                "0",
                "--no-sync",
            ]
        )
        == 1
    )
    assert not path.exists()
    with pytest.raises(SystemExit) as error:
        main(["--timeout", "0", "list"])
    assert error.value.code == 2


def test_list_and_module_version(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "missing.json"), "list"]) == 0
    assert "No devices" in capsys.readouterr().out
