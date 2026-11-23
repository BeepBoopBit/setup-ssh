import subprocess

import pytest

from setup_ssh import local_setup
from setup_ssh.state import SetupError


@pytest.fixture
def commands(monkeypatch):
    calls = []
    monkeypatch.setattr(local_setup.sys, "platform", "linux")
    monkeypatch.setattr(local_setup.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(local_setup.shutil, "which", lambda program: program)

    def run(command, *, capture=False):
        calls.append(command)
        output = {
            "systemctl": "ssh.service disabled enabled\n",
            "sudo ufw status": "Status: active\n",
        }
        return subprocess.CompletedProcess(
            command, 0, output.get(" ".join(command), output.get(command[0], ""))
        )

    monkeypatch.setattr(local_setup, "run", run)
    return calls


@pytest.mark.parametrize("dry_run,approved", [(True, True), (False, False)])
def test_preview_and_decline_do_not_invoke_sudo(commands, dry_run, approved):
    local_setup.prepare_local(["10.10.0.20"], 22, dry_run, False, lambda _: approved)
    assert [command[0] for command in commands] == ["systemctl"]


def test_approved_setup_only_opens_specified_peer_addresses(commands):
    local_setup.prepare_local(
        ["10.10.0.20", "10.10.0.20", "10.10.0.23"], 22, False, False, lambda _: True
    )
    assert commands[1:] == [
        ["sudo", "ufw", "status"],
        ["sudo", "ssh-keygen", "-A"],
        ["sudo", "systemctl", "enable", "--now", "ssh.service"],
        ["sudo", "ufw", "allow", "from", "10.10.0.20", "to", "any", "port", "22", "proto", "tcp"],
        ["sudo", "ufw", "allow", "from", "10.10.0.23", "to", "any", "port", "22", "proto", "tcp"],
    ]


def test_batch_cannot_apply_system_changes(commands):
    with pytest.raises(SetupError, match="interactive approval"):
        local_setup.prepare_local(["10.10.0.20"], 22, False, True, lambda _: True)
    assert commands == []
