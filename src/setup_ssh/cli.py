from __future__ import annotations

import argparse
import getpass
import socket
import sys
from pathlib import Path

import paramiko

from . import __version__
from .fleet import Check, Fleet
from .state import Device, Inventory, SetupError, default_path
from .transport import (
    Connector,
    current_account_matches,
    diagnostics,
    doctor_instructions,
    local_platform,
)


def confirm(message: str, default: bool = False) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(message + suffix).strip().lower()
    return default if not answer else answer in {"yes", "y"}


def prompt(label: str, default: str = "") -> str:
    value = input(label + (f" [{default}]" if default else "") + ": ").strip()
    return value or default


def ask_device(inventory: Inventory, *, local: bool = False, args=None) -> Device:
    def value(name: str, label: str, default: str = "") -> str:
        supplied = getattr(args, name, None)
        return str(supplied) if supplied is not None else prompt(label, default)

    platform_name = (
        local_platform() if local else value("os", "OS (auto/linux/macos/windows)", "auto")
    )
    device = Device(
        name=value("name", "Device name", socket.gethostname().split(".")[0] if local else ""),
        host=value("host", "Address reachable by every device (LAN or NetBird IP/hostname)"),
        user=value("user", "Username", getpass.getuser() if local else ""),
        port=int(value("port", "SSH port", "22")),
        platform=platform_name,
        local=local,
    )
    if local and not current_account_matches(device.user):
        raise SetupError("Use the current account for this computer.")
    inventory.add(device)
    return device


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="setup-ssh",
        description="Set up SSH between Linux, Windows, and macOS devices over LAN or NetBird.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, default=default_path(), help="Inventory JSON path")
    parser.add_argument(
        "--timeout", type=int, default=10, help="Connection timeout in seconds (default: 10)"
    )
    parser.add_argument("--batch", action="store_true", help="No password or host-trust prompts")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Guided enrollment and setup for a new group")
    add = commands.add_parser("add", help="Enroll a device, then synchronize the group")
    add.add_argument("--name")
    add.add_argument("--host", help="LAN or NetBird address")
    add.add_argument("--user")
    add.add_argument("--port", type=int)
    add.add_argument("--os", choices=["auto", "linux", "macos", "windows"])
    add.add_argument("--local", action="store_true", help="This is the current computer/account")
    add.add_argument("--no-sync", action="store_true", help="Save the device and set up later")
    sync = commands.add_parser("sync", help="Reconcile keys and aliases, then test every direction")
    sync.add_argument(
        "--skip-offline",
        action="store_true",
        help="Update and check reachable devices; keep offline devices enrolled for later",
    )
    sync.add_argument("--no-check", action="store_true", help="Skip connection tests")
    sync.add_argument(
        "--dry-run", action="store_true", help="Show the saved plan without connecting"
    )
    commands.add_parser("check", help="Test all connection directions without editing SSH files")
    commands.add_parser("list", help="List enrolled devices")
    commands.add_parser("plan", help="Show the saved plan without connecting or modifying files")
    remove = commands.add_parser("remove", help="Remove a device and revoke its key on peers")
    remove.add_argument("name")
    doctor = commands.add_parser(
        "doctor", help="Show prerequisites and SSH server setup instructions"
    )
    doctor.add_argument("--os", choices=["linux", "macos", "windows"])
    return parser


def show_plan(inventory: Inventory) -> None:
    print("All-to-all SSH access for these accounts:")
    for device in inventory.devices:
        kind = "this computer" if device.local else device.platform
        print(f"  {device.name:16} {device.user}@{device.host}:{device.port} ({kind})")
    count = len(inventory.devices)
    print(f"{count} devices; {count * (count - 1)} connection directions.")
    print(f"Dedicated key per device: ~/.ssh/setup-ssh/{inventory.group_id}/id_ed25519")
    print("New keys have no passphrase, allowing unattended SSH. Private keys stay on each device.")
    print("Sync merges managed entries, preserves unrelated entries, and tests each direction.")


def report(checks: list[Check]) -> int:
    failures = sum(not check.ok for check in checks)
    if checks:
        print(f"\n{len(checks) - failures}/{len(checks)} SSH connections passed.")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 300:
        parser.error("--timeout must be between 1 and 300 seconds")
    try:
        if args.command == "doctor":
            print("\n".join(diagnostics()))
            print("\n" + doctor_instructions(args.os or local_platform()))
            print(
                "\nNetBird: peers must be connected, and access policies must allow your SSH TCP "
                "port in both directions. This tool uses the OS OpenSSH server over NetBird. "
                "NetBird's built-in SSH server uses a separate authentication flow."
            )
            return 0
        path = args.config.expanduser().absolute()
        inventory = Inventory.load(path)
        if args.command == "list":
            if not inventory.devices:
                print("No devices yet. Run setup-ssh init.")
            else:
                for device in inventory.devices:
                    state = "key collected" if device.public_key else "not set up"
                    print(
                        f"{device.name:16} {device.user}@{device.host}:{device.port} "
                        f"{device.platform:8} {state}"
                        + (" (this computer)" if device.local else "")
                    )
            print(f"Inventory: {path}")
            return 0
        if args.command == "plan" or (args.command == "sync" and args.dry_run):
            show_plan(inventory)
            return 0
        connector = Connector(
            inventory,
            timeout=args.timeout,
            batch=args.batch,
            confirm=confirm,
            save=lambda: inventory.save(path),
        )
        fleet = Fleet(inventory, path, connector)
        if args.command == "init":
            if args.batch:
                raise SetupError(
                    "init is interactive. Use add with explicit options and --no-sync for batch enrollment."
                )
            if inventory.devices:
                raise SetupError("This group already has devices. Use add or sync.")
            print("Use LAN or NetBird addresses that every enrolled device can reach.")
            if confirm("Include this computer?", default=True):
                ask_device(inventory, local=True)
                fleet.save()
            while True:
                print("\nAdd a device:")
                ask_device(inventory)
                fleet.save()
                if not confirm("Add another device?"):
                    break
            show_plan(inventory)
            return report(fleet.sync()) if len(inventory.devices) >= 2 else 0
        if args.command == "add":
            if args.batch and not all((args.name, args.host, args.user)):
                raise SetupError("Batch enrollment requires --name, --host, and --user.")
            if args.batch:
                args.os = args.os or "auto"
                args.port = 22 if args.port is None else args.port
            device = ask_device(inventory, local=args.local, args=args)
            fleet.save()
            print(f"Saved {device.name} in {path}.")
            if args.no_sync or len(inventory.devices) < 2:
                print("Run setup-ssh sync when the group is ready.")
                return 0
            show_plan(inventory)
            return report(fleet.sync())
        if args.command == "sync":
            show_plan(inventory)
            checks = fleet.sync(check=not args.no_check, skip_offline=args.skip_offline)
            if args.no_check:
                command = "sync --skip-offline" if args.skip_offline else "check"
                print(f"SSH configuration updated. Run setup-ssh {command} to verify connections.")
            return report(checks)
        if args.command == "check":
            return report(fleet.check())
        if args.command == "remove":
            device = inventory.find(args.name)
            errors = fleet.remove(device)
            for error in errors:
                print(f"  {error}", file=sys.stderr)
            return 1 if errors else 0
    except KeyboardInterrupt:
        print("\nStopped. Saved enrollment is retained; rerun sync to resume.", file=sys.stderr)
        return 130
    except EOFError:
        print("Input ended. For unattended runs use --batch.", file=sys.stderr)
        return 1
    except (SetupError, OSError, ValueError, paramiko.SSHException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0
