"""Public inventory only: account passwords and private keys never go here."""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


class SetupError(Exception):
    """An actionable error safe to display to the user."""


def identifier() -> str:
    return uuid.uuid4().hex[:12]


def default_path() -> Path:
    if os.environ.get("SETUP_SSH_CONFIG"):
        return Path(os.environ["SETUP_SSH_CONFIG"]).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "setup-ssh" / "inventory.json"


def validate_key(value: str) -> str:
    parts = value.strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise SetupError("Expected an Ed25519 public key from ssh-keygen.")
    try:
        raw = base64.b64decode(parts[1], validate=True)
    except (ValueError, TypeError) as exc:
        raise SetupError("Invalid public key encoding.") from exc
    # SSH wire format: length + algorithm + length + 32-byte public key.
    if raw[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" or len(raw) != 51:
        raise SetupError("Invalid Ed25519 public key.")
    return f"{parts[0]} {parts[1]}"


@dataclass
class Device:
    name: str
    host: str
    user: str
    port: int = 22
    platform: str = "auto"
    local: bool = False
    id: str = field(default_factory=identifier)
    public_key: str | None = None
    host_key: dict[str, str] | None = None

    def validate(self) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}", self.name):
            raise SetupError("Device names need 1–63 letters, digits, underscores, or hyphens.")
        if not re.fullmatch(r"[0-9a-f]{12}", self.id):
            raise SetupError(f"Invalid device identifier for {self.name}.")
        if (
            not self.host
            or self.host.startswith("-")
            or not re.fullmatch(r"[a-zA-Z0-9_.:%-]+", self.host)
        ):
            raise SetupError("Use a LAN/NetBird IP or hostname, without a URL, username, or port.")
        if not self.user or any(ord(c) < 32 for c in self.user) or '"' in self.user:
            raise SetupError(
                "Provide a valid username without control characters or double quotes."
            )
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise SetupError("SSH port must be between 1 and 65535.")
        if self.platform not in {"auto", "linux", "macos", "windows"}:
            raise SetupError("OS must be auto, linux, macos, or windows.")
        if type(self.local) is not bool:
            raise SetupError("The local field must be true or false.")
        if self.public_key is not None:
            self.public_key = validate_key(self.public_key)
        if self.host_key is not None:
            if not isinstance(self.host_key, dict) or set(self.host_key) != {"type", "data"}:
                raise SetupError(f"Invalid host key for {self.name}.")
            if not all(isinstance(v, str) for v in self.host_key.values()):
                raise SetupError(f"Invalid host key for {self.name}.")
            if not re.fullmatch(r"[a-zA-Z0-9@._+-]+", self.host_key["type"]):
                raise SetupError(f"Invalid host key type for {self.name}.")
            try:
                base64.b64decode(self.host_key["data"], validate=True)
            except ValueError as exc:
                raise SetupError(f"Invalid host key for {self.name}.") from exc


@dataclass
class Inventory:
    group_id: str = field(default_factory=identifier)
    devices: list[Device] = field(default_factory=list)
    version: int = 1

    def validate(self) -> None:
        if self.version != 1 or not re.fullmatch(r"[0-9a-f]{12}", self.group_id):
            raise SetupError("Unsupported or malformed inventory.")
        names: set[str] = set()
        ids: set[str] = set()
        accounts: set[tuple[str, int, str]] = set()
        for device in self.devices:
            device.validate()
            account = (device.host.lower(), device.port, device.user.lower())
            if device.name.lower() in names or device.id in ids or account in accounts:
                raise SetupError(f"Duplicate device name or account: {device.name}.")
            names.add(device.name.lower())
            ids.add(device.id)
            accounts.add(account)
        if sum(device.local for device in self.devices) > 1:
            raise SetupError("Only one device can be this computer.")

    def add(self, device: Device) -> None:
        self.devices.append(device)
        try:
            self.validate()
        except Exception:
            self.devices.pop()
            raise

    def find(self, name: str) -> Device:
        for device in self.devices:
            if device.name.lower() == name.lower():
                return device
        raise SetupError(f"Unknown device: {name}.")

    @classmethod
    def load(cls, path: Path) -> Inventory:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            inventory = cls(
                group_id=data["group_id"],
                devices=[Device(**item) for item in data["devices"]],
                version=data["version"],
            )
            inventory.validate()
            return inventory
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SetupError(f"Cannot read inventory {path}: {exc}") from exc

    def save(self, path: Path) -> None:
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".inventory-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(asdict(self), stream, indent=2)
                stream.write("\n")
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
