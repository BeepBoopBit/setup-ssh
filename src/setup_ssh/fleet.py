"""Reconcile the group, retaining public state so interrupted runs can resume."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import paramiko

from .render import script
from .state import Device, Inventory, SetupError, validate_key
from .transport import Connector, DeviceUnavailableError, Session


@dataclass
class Check:
    source: str
    destination: str
    ok: bool
    detail: str = ""


class Fleet:
    def __init__(
        self,
        inventory: Inventory,
        path: Path,
        connector: Connector,
        output: Callable[[str], None] = print,
    ):
        self.inventory = inventory
        self.path = path
        self.connector = connector
        self.output = output

    def save(self) -> None:
        self.inventory.save(self.path)

    def connect_all(self, *, skip_offline: bool = False) -> dict[str, Session]:
        sessions = {}
        errors = []
        try:
            for device in sorted(self.inventory.devices, key=lambda item: not item.local):
                self.output(
                    f"Connecting to {device.name} ({device.user}@{device.host}:{device.port})…"
                )
                try:
                    sessions[device.id] = self.connector.open(device)
                except DeviceUnavailableError as exc:
                    if skip_offline:
                        self.output(f"Skipping offline {device.name}: {exc}")
                    else:
                        errors.append(str(exc))
                except SetupError as exc:
                    errors.append(str(exc))
            if errors:
                raise SetupError("Cannot reach the complete group:\n  " + "\n  ".join(errors))
            if not sessions:
                raise SetupError("No devices are reachable; nothing was updated.")
            return sessions
        except BaseException:
            for session in sessions.values():
                session.close()
            raise

    def sync(self, *, check: bool = True, skip_offline: bool = False) -> list[Check]:
        if not self.inventory.devices:
            raise SetupError("No devices are enrolled. Run setup-ssh init or add first.")
        sessions = self.connect_all(skip_offline=skip_offline)
        try:
            available = [device for device in self.inventory.devices if device.id in sessions]
            # Collect reachable peers' keys before changing anyone's authorization list.
            for device in available:
                self.output(f"Preparing {device.name}'s dedicated key…")
                result = sessions[device.id].run(script(self.inventory, device, "prepare"))
                if result.code:
                    raise SetupError(
                        f"{device.name}: {result.stderr.strip() or result.stdout.strip()}"
                    )
                values = [
                    line[14:]
                    for line in result.stdout.splitlines()
                    if line.startswith("SETUP_SSH_KEY=")
                ]
                if len(values) != 1:
                    raise SetupError(f"{device.name}: did not return a public key.")
                public = validate_key(values[0])
                if device.public_key and device.public_key != public:
                    raise SetupError(
                        f"{device.name}'s managed key changed. Restore its original key, "
                        "or remove and re-add the device to replace it."
                    )
                device.public_key = public
                self.save()
            # Keep saved keys and aliases for initialized offline peers. An offline
            # peer without keys stays enrolled, but cannot participate until a later sync.
            configured = self.inventory
            if skip_offline:
                configured = replace(
                    self.inventory,
                    devices=[d for d in self.inventory.devices if d.public_key and d.host_key],
                )
            errors = []
            for device in available:
                self.output(f"Updating keys and SSH aliases on {device.name}…")
                result = sessions[device.id].run(script(configured, device, "apply"))
                if result.code:
                    errors.append(
                        f"{device.name}: {result.stderr.strip() or result.stdout.strip()}"
                    )
            if errors:
                raise SetupError(
                    "Some updates failed; successful updates are retained. Fix these and rerun sync:\n  "
                    + "\n  ".join(errors)
                )
            self.save()
            skipped = [
                device.name for device in self.inventory.devices if device.id not in sessions
            ]
            if skipped:
                self.output(
                    f"Updated {len(available)}/{len(self.inventory.devices)} devices. "
                    f"Still pending: {', '.join(skipped)}. Run sync when they are online. "
                    "Connection checks cover only the available devices."
                )
            return self.check_sessions(sessions) if check else []
        finally:
            for session in sessions.values():
                session.close()

    def check_sessions(self, sessions: dict[str, Session]) -> list[Check]:
        checks = []
        available = [device for device in self.inventory.devices if device.id in sessions]
        for source in available:
            for destination in available:
                if source.id == destination.id:
                    continue
                try:
                    result = sessions[source.id].run(
                        script(
                            self.inventory,
                            source,
                            "check",
                            destination=destination,
                            timeout=self.connector.timeout,
                        ),
                        timeout=self.connector.timeout + 15,
                    )
                    ok = result.code == 0 and "setup-ssh-ok" in result.stdout.splitlines()
                    detail = (
                        ""
                        if ok
                        else (result.stderr or result.stdout or f"exit {result.code}").strip()
                    )
                except (SetupError, OSError, paramiko.SSHException) as exc:
                    ok, detail = False, str(exc)
                check = Check(source.name, destination.name, ok, detail)
                checks.append(check)
                self.output(f"  {source.name} -> {destination.name}: {'OK' if ok else 'FAILED'}")
                if detail:
                    self.output(f"    {detail}")
        return checks

    def check(self) -> list[Check]:
        if len(self.inventory.devices) < 2:
            raise SetupError("Add at least two devices first.")
        if any(not device.public_key or not device.host_key for device in self.inventory.devices):
            raise SetupError("The group has not completed setup. Run setup-ssh sync first.")
        sessions = self.connect_all()
        try:
            return self.check_sessions(sessions)
        finally:
            for session in sessions.values():
                session.close()

    def remove(self, device: Device) -> list[str]:
        # Save revocation intent first: a future sync must never reauthorize this device.
        self.inventory.devices.remove(device)
        self.save()
        if device.public_key is None:
            self.output(f"Removed {device.name}; it had no distributed managed key.")
            return []
        sessions = {}
        errors = []
        try:
            for survivor in self.inventory.devices:
                try:
                    sessions[survivor.id] = self.connector.open(survivor)
                except SetupError as exc:
                    errors.append(str(exc))
            # Incomplete enrollment never distributed any key if none were collected.
            ready = all(d.public_key and d.host_key for d in self.inventory.devices)
            for survivor in self.inventory.devices:
                session = sessions.get(survivor.id)
                if session is None:
                    continue
                if not ready:
                    errors.append(f"{survivor.name}: incomplete setup; run sync to reconcile it.")
                    continue
                result = session.run(script(self.inventory, survivor, "apply"))
                if result.code:
                    errors.append(
                        f"{survivor.name}: {result.stderr.strip() or result.stdout.strip()}"
                    )
                else:
                    self.output(f"Revoked {device.name} on {survivor.name}.")
            try:
                session = self.connector.open(device)
                try:
                    result = session.run(script(self.inventory, device, "remove"))
                    if result.code:
                        self.output(f"Could not clean {device.name}: {result.stderr.strip()}")
                    else:
                        self.output(f"Removed the group's entries from {device.name}.")
                finally:
                    session.close()
            except SetupError as exc:
                self.output(f"Removed device could not be cleaned: {exc}")
            if errors:
                self.output(
                    "The device is removed from the inventory. Revocation is incomplete on some "
                    "peers; run sync when they are reachable."
                )
            return errors
        finally:
            for session in sessions.values():
                session.close()
