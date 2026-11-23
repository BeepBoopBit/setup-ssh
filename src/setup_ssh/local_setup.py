"""Prepare the local Linux SSH server without elevating the enrollment process."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from ipaddress import ip_address

from .state import SetupError


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=capture, text=True, check=False)
    if result.returncode:
        detail = f" {result.stderr.strip()}" if capture and result.stderr else ""
        raise SetupError(f"Command failed ({result.returncode}): {shlex.join(command)}.{detail}")
    return result


def prepare_local(
    sources: list[str],
    port: int,
    dry_run: bool,
    batch: bool,
    confirm: Callable[[str], bool],
) -> None:
    if not sys.platform.startswith("linux"):
        raise SetupError("prepare-local supports Linux with systemd. See setup-ssh doctor.")
    if not 1 <= port <= 65535:
        raise SetupError("SSH port must be between 1 and 65535.")
    if batch and not dry_run:
        raise SetupError(
            "prepare-local requires interactive approval. Use --dry-run in batch mode."
        )
    if not dry_run and os.geteuid() == 0:
        raise SetupError("Run prepare-local as your normal user. It calls sudo for system changes.")

    peers = {}
    for source in sources:
        address = ip_address(source)
        if (
            address.is_unspecified
            or address.is_multicast
            or getattr(address, "scope_id", None) is not None
        ):
            raise SetupError(f"Use an unscoped peer IP address: {source}")
        peers[str(address)] = None

    for program in ("systemctl", "ssh-keygen", "sudo"):
        if not shutil.which(program):
            raise SetupError(f"{program} is required. See setup-ssh doctor.")
    units = run(
        [
            "systemctl",
            "list-unit-files",
            "--type=service",
            "--no-legend",
            "--no-pager",
            "sshd.service",
            "ssh.service",
        ],
        capture=True,
    )
    available = {line.split()[0] for line in units.stdout.splitlines() if line.strip()}
    unit = next((name for name in ("sshd.service", "ssh.service") if name in available), None)
    if unit is None:
        raise SetupError("OpenSSH server service was not found. See setup-ssh doctor.")

    status = ["sudo", "ufw", "status"]
    if peers and not shutil.which("ufw"):
        raise SetupError("UFW was not found. See setup-ssh doctor to configure the local firewall.")

    rules = [
        ["sudo", "ufw", "allow", "from", peer, "to", "any", "port", str(port), "proto", "tcp"]
        for peer in peers
    ]
    commands = [
        ["sudo", "ssh-keygen", "-A"],
        ["sudo", "systemctl", "enable", "--now", unit],
        *rules,
    ]
    print("Local SSH setup will run:")
    for command in ([status] if rules else []) + commands:
        print(f"  {shlex.join(command)}")
    if not rules:
        print("Supply --allow-from for each peer to add UFW rules.")
    if dry_run:
        return
    if not confirm("Apply these local changes?"):
        return

    if rules:
        result = run(status, capture=True)
        if "Status: active" not in result.stdout.splitlines():
            raise SetupError(
                "UFW must be active before adding rules. Review its configuration first."
            )
    for command in commands:
        run(command)
    print("Local setup applied. Continue enrollment from the computer managing the group.")
