import os
import shutil
import subprocess

import paramiko
import pytest

from setup_ssh.state import Device, Inventory, SetupError
from setup_ssh.transport import Connector, current_account_matches


class EmptyAgent:
    def get_keys(self):
        return []

    def close(self):
        pass


@pytest.mark.skipif(os.name == "nt" or not shutil.which("ssh-keygen"), reason="Requires POSIX")
def test_locked_existing_private_key_does_not_block_password_bootstrap(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(paramiko, "Agent", EmptyAgent)
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "test-passphrase",
            "-f",
            str(ssh / "id_ed25519"),
        ],
        check=True,
    )
    connector = Connector(Inventory(), confirm=lambda _: False, save=lambda: None)
    assert list(connector.key_candidates()) == []


def test_batch_unknown_host_never_trusts_or_saves(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    saved = []
    connector = Connector(
        Inventory(), batch=True, confirm=lambda _: True, save=lambda: saved.append(True)
    )
    device = Device("linux", "192.0.2.5", "alex")
    with pytest.raises(SetupError, match="unknown host key"):
        connector.verify(device, paramiko.RSAKey.generate(2048))
    assert device.host_key is None
    assert saved == []


def test_windows_local_account_accepts_domain_qualified_name(monkeypatch):
    monkeypatch.setattr("setup_ssh.transport.local_platform", lambda: "windows")
    monkeypatch.setattr("setup_ssh.transport.getpass.getuser", lambda: "alex")
    monkeypatch.setenv("USERDOMAIN", "ACME")
    assert current_account_matches("ACME\\alex")
    assert current_account_matches("Alex")
    assert not current_account_matches("OTHER\\alex")
