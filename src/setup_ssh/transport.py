"""Local execution and SSH sessions with explicit, pinned host-key trust."""

from __future__ import annotations

import base64
import getpass
import hashlib
import os
import platform
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import paramiko
from cryptography.exceptions import UnsupportedAlgorithm

from .state import Device, Inventory, SetupError


class DeviceUnavailableError(SetupError):
    """The device's SSH port could not be reached."""


def fingerprint(key: paramiko.PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def local_platform() -> str:
    name = platform.system()
    try:
        return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}[name]
    except KeyError as exc:
        raise SetupError(f"Unsupported operating system: {name}.") from exc


def current_account_matches(user: str) -> bool:
    current = getpass.getuser()
    if local_platform() != "windows":
        return user == current
    names = {current.casefold()}
    if os.environ.get("USERDOMAIN"):
        names.add(f"{os.environ['USERDOMAIN']}\\{current}".casefold())
    if os.environ.get("USERDNSDOMAIN"):
        names.add(f"{current}@{os.environ['USERDNSDOMAIN']}".casefold())
    return user.casefold() in names


def verify_local_address(device: Device) -> None:
    """Avoid applying a copied inventory's local entry on the wrong computer."""
    for family, kind, protocol, _, address in socket.getaddrinfo(
        device.host, 0, type=socket.SOCK_STREAM
    ):
        try:
            with socket.socket(family, kind, protocol) as sock:
                sock.bind(address)
            return
        except OSError:
            continue
    raise SetupError(
        f"{device.name}: {device.host} does not belong to this computer. "
        "Use its current LAN/NetBird address, or enroll it without --local."
    )


@dataclass
class Result:
    code: int
    stdout: str = ""
    stderr: str = ""


class Session:
    def __init__(self, device: Device, transport: paramiko.Transport | None = None):
        self.device = device
        self.transport = transport

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()

    def command(self, command: str | list[str], data: str = "", timeout: int = 60) -> Result:
        if self.transport is None:
            if isinstance(command, str):
                raise ValueError("Local commands must be an argument list.")
            try:
                process = subprocess.run(
                    command, input=data, capture_output=True, text=True, timeout=timeout
                )
            except FileNotFoundError as exc:
                raise SetupError(f"Missing {command[0]}. Run setup-ssh doctor.") from exc
            except subprocess.TimeoutExpired as exc:
                raise SetupError(
                    f"{self.device.name}: command timed out after {timeout}s."
                ) from exc
            return Result(process.returncode, process.stdout, process.stderr)
        if not isinstance(command, str):
            raise ValueError("Remote commands must be strings.")
        channel = self.transport.open_session(timeout=timeout)
        channel.settimeout(timeout)
        try:
            channel.exec_command(command)
            if data:
                channel.sendall(data.encode("utf-8"))
            channel.shutdown_write()
            stdout = bytearray()
            stderr = bytearray()
            deadline = time.monotonic() + timeout
            while True:
                while channel.recv_ready():
                    stdout.extend(channel.recv(65536))
                while channel.recv_stderr_ready():
                    stderr.extend(channel.recv_stderr(65536))
                if channel.exit_status_ready() and not (
                    channel.recv_ready() or channel.recv_stderr_ready()
                ):
                    break
                if time.monotonic() > deadline:
                    raise SetupError(f"{self.device.name}: command timed out after {timeout}s.")
                time.sleep(0.01)
            return Result(
                channel.recv_exit_status(),
                stdout.decode("utf-8-sig", errors="replace"),
                stderr.decode("utf-8-sig", errors="replace"),
            )
        finally:
            channel.close()

    def run(self, script: str, timeout: int = 60) -> Result:
        if self.device.platform == "windows":
            # The fixed loader keeps cmd.exe's 8191-character command limit out of the way.
            loader = (
                "[Console]::InputEncoding = New-Object Text.UTF8Encoding($false); "
                "[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false); "
                "$payload = [Console]::In.ReadLine(); "
                "& ([ScriptBlock]::Create([Text.Encoding]::UTF8.GetString("
                "[Convert]::FromBase64String($payload))))"
            )
            encoded = base64.b64encode(loader.encode("utf-16le")).decode()
            args = [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ]
            command = subprocess.list2cmdline(args) if self.transport else args
            # Windows OpenSSH may keep stdin open after SSH EOF. A framed line
            # completes without EOF, even for scripts beyond cmd.exe's size limit.
            data = base64.b64encode(script.encode("utf-8")).decode() + "\n"
        else:
            command = "sh -s" if self.transport else ["sh", "-s"]
            data = script
        return self.command(command, data, timeout)

    def detect_platform(self) -> None:
        if self.transport is None:
            actual = local_platform()
            if self.device.platform not in {"auto", actual}:
                raise SetupError(f"This computer runs {actual}, not {self.device.platform}.")
            self.device.platform = actual
            return
        if self.device.platform != "auto":
            return
        result = self.command("uname -s", timeout=15)
        name = result.stdout.strip()
        if result.code == 0 and name in {"Linux", "Darwin"}:
            self.device.platform = {"Linux": "linux", "Darwin": "macos"}[name]
            return
        result = self.command(
            "powershell.exe -NoLogo -NoProfile -NonInteractive -Command "
            '"[Environment]::OSVersion.Platform.ToString()"',
            timeout=15,
        )
        if result.code == 0 and "Win32NT" in result.stdout:
            self.device.platform = "windows"
            return
        raise SetupError(
            f"Cannot detect {self.device.name}'s OS. Set --os linux, macos, or windows."
        )


class Connector:
    def __init__(
        self,
        inventory: Inventory,
        *,
        timeout: int = 10,
        batch: bool = False,
        confirm: Callable[[str], bool],
        save: Callable[[], None],
        password: Callable[[str], str] = getpass.getpass,
    ):
        self.inventory = inventory
        self.timeout = timeout
        self.batch = batch
        self.confirm = confirm
        self.save = save
        self.password = password

    def verify(self, device: Device, key: paramiko.PKey) -> None:
        offered = {"type": key.get_name(), "data": key.get_base64()}
        if device.host_key is not None:
            if device.host_key != offered:
                raise SetupError(
                    f"{device.name}: SSH host key CHANGED ({fingerprint(key)}). "
                    "Check the device identity before updating its inventory pin."
                )
            return
        known = paramiko.HostKeys()
        path = Path.home() / ".ssh" / "known_hosts"
        if path.is_file():
            try:
                known.load(str(path))
            except (OSError, paramiko.SSHException, ValueError) as exc:
                raise SetupError(f"Cannot read {path}: {exc}") from exc
        host = device.host if device.port == 22 else f"[{device.host}]:{device.port}"
        existing = known.lookup(host)
        trusted = existing is not None and key.get_name() in existing
        if trusted and existing[key.get_name()] != key:
            raise SetupError(
                f"{device.name}: host key conflicts with {path}. Check the device identity."
            )
        if not trusted:
            if self.batch:
                raise SetupError(f"{device.name}: unknown host key. Run sync interactively first.")
            if not self.confirm(
                f"{device.name} ({device.host}:{device.port})\n"
                f"  {key.get_name()} {fingerprint(key)}\n"
                "Compare this fingerprint with the device's host key. Trust this device?"
            ):
                raise SetupError(f"{device.name}: host key was not trusted.")
        device.host_key = offered
        self.save()

    def handshake(self, device: Device) -> paramiko.Transport:
        try:
            sock = socket.create_connection((device.host, device.port), timeout=self.timeout)
        except OSError as exc:
            raise DeviceUnavailableError(
                f"{device.name} ({device.host}:{device.port}): {exc}. "
                "Check SSH server, address, firewall, and NetBird TCP access policy. "
                "Run setup-ssh doctor for setup instructions."
            ) from exc
        transport = paramiko.Transport(sock)
        transport.banner_timeout = self.timeout
        transport.auth_timeout = self.timeout
        # Keep a stored host key's algorithm stable even if SSH defaults change.
        if device.host_key:
            key_type = device.host_key["type"]
            options = transport.get_security_options()
            allowed = tuple(
                name
                for name in options.key_types
                if name == key_type or (key_type == "ssh-rsa" and name.startswith("rsa-sha2-"))
            )
            if allowed:
                options.key_types = allowed
        try:
            transport.start_client(timeout=self.timeout)
            if not transport.is_active():
                raise SetupError(f"{device.name}: SSH handshake did not complete.")
            self.verify(device, transport.get_remote_server_key())
            transport.set_keepalive(30)
            return transport
        except BaseException:
            transport.close()
            raise

    def key_candidates(self):
        seen: set[bytes] = set()
        # Also works while revoking this computer after removing its local-device entry.
        paths = [Path.home() / ".ssh" / "setup-ssh" / self.inventory.group_id / "id_ed25519"]
        for path in paths:
            if path.is_file():
                try:
                    key = paramiko.PKey.from_path(path)
                    seen.add(key.asbytes())
                    yield key
                except (
                    OSError,
                    ValueError,
                    TypeError,
                    UnsupportedAlgorithm,
                    paramiko.SSHException,
                    paramiko.pkey.UnknownKeyType,
                ):
                    pass
        agent = paramiko.Agent()
        try:
            for key in agent.get_keys():
                if key.asbytes() not in seen:
                    seen.add(key.asbytes())
                    yield key
        finally:
            agent.close()
        for name in ("id_ed25519", "id_ecdsa", "id_rsa"):
            path = Path.home() / ".ssh" / name
            if not path.is_file():
                continue
            try:
                key = paramiko.PKey.from_path(path)
                if key.asbytes() not in seen:
                    seen.add(key.asbytes())
                    yield key
            except (
                OSError,
                ValueError,
                TypeError,
                UnsupportedAlgorithm,
                paramiko.SSHException,
                paramiko.pkey.UnknownKeyType,
            ):
                continue

    def open(self, device: Device) -> Session:
        transport = None
        try:
            if device.local:
                if not current_account_matches(device.user):
                    raise SetupError(
                        f"Local device user is {device.user}; run this inventory as that account."
                    )
                verify_local_address(device)
                # Verify the incoming server, but perform local writes as the current account.
                transport = self.handshake(device)
                transport.close()
                transport = None
                session = Session(device)
            else:
                transport = self.handshake(device)
                candidates = self.key_candidates()
                try:
                    for key in candidates:
                        if not transport.is_active():
                            break
                        try:
                            transport.auth_publickey(device.user, key)
                        except (paramiko.AuthenticationException, paramiko.SSHException):
                            continue
                        if transport.is_authenticated():
                            break
                finally:
                    candidates.close()
                if not transport.is_authenticated():
                    if self.batch:
                        raise SetupError(
                            f"{device.name}: key login failed; run sync interactively."
                        )
                    transport.close()
                    transport = self.handshake(device)
                    secret = self.password(f"Password for {device.user}@{device.host}: ")
                    try:
                        transport.auth_password(device.user, secret)
                    finally:
                        del secret
                    if not transport.is_authenticated():
                        raise SetupError(
                            f"{device.name}: authentication requires additional steps."
                        )
                session = Session(device, transport)
            session.detect_platform()
            return session
        except SetupError:
            if transport:
                transport.close()
            raise
        except (OSError, paramiko.SSHException, EOFError) as exc:
            if transport:
                transport.close()
            raise SetupError(
                f"{device.name} ({device.host}:{device.port}): {exc}. "
                "Check SSH server, address, firewall, and NetBird TCP access policy. "
                "Run setup-ssh doctor for setup instructions."
            ) from exc
        except BaseException:
            if transport:
                transport.close()
            raise


def doctor_instructions(target: str) -> str:
    if target == "windows":
        return """Windows — run PowerShell as Administrator:
  Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
  Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
  Start-Service sshd
  Set-Service sshd -StartupType Automatic
  Get-NetFirewallRule -Name OpenSSH-Server-In-TCP
If that firewall rule is missing, create it for the interface/network you use.
Use a local or Active Directory account with an account password (not a Windows Hello PIN).
Default Windows administrator accounts share administrators_authorized_keys."""
    if target == "macos":
        return """macOS:
  System Settings → General → Sharing → Remote Login → On
  Allow access for the account you will use.
  OpenSSH client and ssh-keygen are included with macOS."""
    return """Linux — install the OpenSSH client and server for your distribution:
  Debian/Ubuntu: sudo apt install openssh-client openssh-server
                sudo systemctl enable --now ssh
  Fedora:        sudo dnf install openssh-clients openssh-server
                sudo systemctl enable --now sshd
Allow the chosen SSH port through your host firewall on the network you use.
Use your regular account; root SSH login may be disabled by server policy."""


def diagnostics() -> list[str]:
    lines = []
    for name in ("ssh", "ssh-keygen", "netbird"):
        found = shutil.which(name)
        lines.append(f"{name}: {found or 'not installed / not on PATH'}")
    if shutil.which("netbird"):
        try:
            process = subprocess.run(
                ["netbird", "status"], capture_output=True, text=True, timeout=10
            )
            lines.append(process.stdout.strip() or process.stderr.strip())
        except (OSError, subprocess.TimeoutExpired) as exc:
            lines.append(f"NetBird status unavailable: {exc}")
    return lines
