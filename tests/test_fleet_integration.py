"""Real SSH protocol + real OpenSSH clients, with every filesystem write in tmp_path."""

import getpass
import os
import shutil
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import paramiko
import pytest

from setup_ssh import render
from setup_ssh.cli import main
from setup_ssh.fleet import Fleet
from setup_ssh.state import Device, Inventory, SetupError
from setup_ssh.transport import Connector

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not shutil.which("ssh-keygen"), reason="Requires POSIX OpenSSH"
)


class SSHServer(paramiko.ServerInterface):
    def __init__(self, home, user):
        self.home = home
        self.user = user
        self.command = None
        self.ready = threading.Event()

    def get_allowed_auths(self, username):
        return "publickey,password"

    def check_auth_password(self, username, password):
        if username == self.user and password == "temporary-test-password":
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        path = self.home / ".ssh" / "authorized_keys"
        if username != self.user or not path.exists():
            return paramiko.AUTH_FAILED
        for line in path.read_text().splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[:2] == [key.get_name(), key.get_base64()]:
                return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return (
            paramiko.OPEN_SUCCEEDED
            if kind == "session"
            else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        )

    def check_channel_exec_request(self, channel, command):
        # Each connection opens sequential command channels; capture one request at a time.
        channel.test_command = command.decode()
        channel.test_ready = threading.Event()
        channel.test_ready.set()
        return True


class TestPeer:
    __test__ = False

    def __init__(self, home, name):
        self.home = home
        home.mkdir()
        self.user = "fleet-test"
        self.name = name
        self.host_key = paramiko.RSAKey.generate(2048)
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(30)
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.stopped = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=12)
        self.transports = []
        self.futures = []
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def accept(self):
        while not self.stopped.is_set():
            try:
                sock, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.futures.append(self.pool.submit(self.serve, sock))

    def serve(self, sock):
        transport = paramiko.Transport(sock)
        self.transports.append(transport)
        transport.add_server_key(self.host_key)
        server = SSHServer(self.home, self.user)
        try:
            transport.start_server(server=server)
            while transport.is_active() and not self.stopped.is_set():
                channel = transport.accept(timeout=0.1)
                if channel is None:
                    continue
                # Run asynchronously so a source's ssh subprocess can reach another peer.
                self.futures.append(self.pool.submit(self.execute, channel))
        except (EOFError, OSError, paramiko.SSHException):
            pass  # Unauthenticated host-key probes and clients closing are expected.
        finally:
            transport.close()

    def execute(self, channel):
        try:
            # Paramiko accepts the channel before delivering its exec request.
            for _ in range(500):
                if hasattr(channel, "test_command"):
                    break
                self.stopped.wait(0.01)
            if not hasattr(channel, "test_command"):
                return
            command = channel.test_command
            data = bytearray()
            if command == "sh -s":
                while True:
                    chunk = channel.recv(65536)
                    if not chunk:
                        break
                    data.extend(chunk)
            # A real account's tilde expands to its passwd home. The fixtures have fake
            # homes, so patch generated *public* config paths before writing them.
            process = subprocess.run(
                command,
                shell=True,
                input=data,
                env={**os.environ, "HOME": str(self.home)},
                capture_output=True,
                timeout=20,
            )
            for config in (self.home / ".ssh" / "setup-ssh").glob("*/config"):
                value = config.read_text()
                if "~/.ssh" in value:
                    config.write_text(value.replace("~/.ssh", str(self.home / ".ssh")))
            if process.stdout:
                channel.sendall(process.stdout)
            if process.stderr:
                channel.sendall_stderr(process.stderr)
            channel.send_exit_status(process.returncode)
        finally:
            channel.close()

    def close(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        self.listener.close()
        for transport in self.transports:
            transport.close()
        self.thread.join(timeout=5)
        self.pool.shutdown(wait=True)
        for future in self.futures:
            future.result()


@pytest.fixture
def group(tmp_path, monkeypatch):
    peers = [TestPeer(tmp_path / f"home {index}", str(index)) for index in range(3)]
    inventory = Inventory()
    for index, peer in enumerate(peers):
        inventory.add(Device(f"device{index}", "127.0.0.1", peer.user, port=peer.port))
    path = tmp_path / "inventory.json"
    prompts = []

    def confirm(message):
        prompts.append(message)
        return True

    connector = Connector(
        inventory,
        timeout=3,
        confirm=confirm,
        save=lambda: inventory.save(path),
        password=lambda _: "temporary-test-password",
    )
    # The coordinator is outside this fixture's three-device group, so bootstrap
    # authenticates with passwords. Peer-to-peer checks use only each peer's key.
    monkeypatch.setattr(connector, "key_candidates", lambda: (key for key in ()))
    fleet = Fleet(inventory, path, connector, output=lambda _: None)
    try:
        yield fleet, peers, prompts
    finally:
        for peer in peers:
            peer.close()


def test_real_three_device_mesh_is_repeatable_and_revocable(group):
    fleet, peers, prompts = group
    checks = fleet.sync()
    assert len(checks) == 6
    assert all(check.ok for check in checks), [(c.source, c.destination, c.detail) for c in checks]
    assert len(prompts) == 3
    roots = [peer.home / ".ssh" / "setup-ssh" / fleet.inventory.group_id for peer in peers]
    keys = [(root / "id_ed25519").read_bytes() for root in roots]
    assert len(set(keys)) == 3
    assert "temporary-test-password" not in fleet.path.read_text()

    checks = fleet.sync()
    assert all(check.ok for check in checks)
    assert len(prompts) == 3  # Host fingerprints were pinned, not reapproved.
    assert keys == [(root / "id_ed25519").read_bytes() for root in roots]

    victim = fleet.inventory.devices[-1]
    public = victim.public_key
    errors = fleet.remove(victim)
    assert not errors
    for peer in peers:
        assert public not in (peer.home / ".ssh" / "authorized_keys").read_text()
    checks = fleet.check()
    assert len(checks) == 2
    assert all(check.ok for check in checks)
    # A final surviving peer must still be reconcilable after removals.
    assert not fleet.remove(fleet.inventory.devices[-1])
    assert fleet.sync() == []


def test_unreachable_group_does_not_generate_partial_keys(group):
    fleet, peers, _ = group
    peers[-1].close()
    with pytest.raises(SetupError, match="complete group"):
        fleet.sync()
    assert not any((peer.home / ".ssh").exists() for peer in peers)


def test_partial_sync_enrolls_new_peer_and_preserves_offline_access(group, monkeypatch, capsys):
    fleet, peers, _ = group
    new_device = fleet.inventory.devices.pop()
    assert all(check.ok for check in fleet.sync())
    offline = fleet.inventory.devices[1]
    offline_files = {
        path: path.read_bytes() for path in (peers[1].home / ".ssh").rglob("*") if path.is_file()
    }
    peers[1].close()
    fleet.inventory.add(new_device)
    fleet.save()
    monkeypatch.setattr("setup_ssh.cli.Connector", lambda *args, **kwargs: fleet.connector)

    assert main(["--config", str(fleet.path), "sync", "--skip-offline"]) == 0
    output = capsys.readouterr().out
    assert "Still pending: device1" in output
    assert "2/2 SSH connections passed" in output
    saved = Inventory.load(fleet.path)
    assert len(saved.devices) == 3
    assert all(device.public_key and device.host_key for device in saved.devices)
    for peer in (peers[0], peers[2]):
        assert offline.public_key in (peer.home / ".ssh" / "authorized_keys").read_text()
        root = peer.home / ".ssh" / "setup-ssh" / fleet.inventory.group_id
        assert "Host device1\n" in (root / "config").read_text()
        assert offline.host_key["data"] in (root / "known_hosts").read_text()
    assert all(path.read_bytes() == value for path, value in offline_files.items())
    assert saved.devices[2].public_key not in (peers[1].home / ".ssh/authorized_keys").read_text()


def test_partial_sync_defers_uninitialized_offline_peer(group):
    fleet, peers, _ = group
    peers[-1].close()
    checks = fleet.sync(skip_offline=True)
    assert len(checks) == 2
    assert all(check.ok for check in checks)
    assert {check.source for check in checks} == {"device0", "device1"}
    saved = Inventory.load(fleet.path)
    assert len(saved.devices) == 3
    assert saved.devices[-1].public_key is None
    assert not (peers[-1].home / ".ssh").exists()
    for peer in peers[:2]:
        config = peer.home / ".ssh/setup-ssh" / fleet.inventory.group_id / "config"
        assert "Host device2\n" not in config.read_text()


def test_partial_sync_fails_if_every_device_is_offline(group):
    fleet, peers, _ = group
    for peer in peers:
        peer.close()
    with pytest.raises(SetupError, match="No devices are reachable"):
        fleet.sync(skip_offline=True)
    assert not any((peer.home / ".ssh").exists() for peer in peers)


def test_full_sync_completes_deferred_enrollment(group, monkeypatch):
    fleet, peers, _ = group
    connect = socket.create_connection

    def temporarily_offline(address, *args, **kwargs):
        if address[1] == peers[-1].port:
            raise ConnectionRefusedError("Test peer is temporarily offline")
        return connect(address, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(socket, "create_connection", temporarily_offline)
        checks = fleet.sync(skip_offline=True)
        assert len(checks) == 2 and all(check.ok for check in checks)
    original_keys = [device.public_key for device in fleet.inventory.devices[:2]]
    checks = fleet.sync()
    assert len(checks) == 6 and all(check.ok for check in checks)
    assert original_keys == [device.public_key for device in fleet.inventory.devices[:2]]
    assert all(device.public_key for device in Inventory.load(fleet.path).devices)


@pytest.mark.parametrize("failure", ["host_key", "password"])
def test_partial_sync_does_not_skip_trust_or_authentication_errors(group, failure):
    fleet, peers, _ = group
    if failure == "host_key":
        transport = fleet.connector.handshake(fleet.inventory.devices[0])
        transport.close()
        peers[0].host_key = paramiko.RSAKey.generate(2048)
    else:
        fleet.connector.password = lambda _: "incorrect-test-password"
    with pytest.raises(SetupError, match="complete group"):
        fleet.sync(skip_offline=True)
    assert not any((peer.home / ".ssh").exists() for peer in peers)


def test_host_key_changes_are_rejected_before_credentials(group):
    fleet, peers, _ = group
    device = fleet.inventory.devices[0]
    transport = fleet.connector.handshake(device)
    transport.close()
    peers[0].host_key = paramiko.RSAKey.generate(2048)
    with pytest.raises(SetupError, match="CHANGED"):
        fleet.connector.open(device)


def test_offline_survivor_keeps_removal_intent(group):
    fleet, peers, _ = group
    assert all(check.ok for check in fleet.sync())
    victim = fleet.inventory.devices[-1]
    peers[0].close()
    assert fleet.remove(victim)
    saved = Inventory.load(fleet.path)
    assert victim.id not in {device.id for device in saved.devices}
    assert victim.public_key not in (peers[1].home / ".ssh" / "authorized_keys").read_text()


def test_including_this_computer_enables_passwordless_management(group, monkeypatch):
    fleet, peers, _ = group
    local = fleet.inventory.devices[0]
    local.local = True
    local.user = getpass.getuser()
    peers[0].user = local.user
    monkeypatch.setenv("HOME", str(peers[0].home))
    monkeypatch.setattr(
        fleet.connector, "key_candidates", Connector.key_candidates.__get__(fleet.connector)
    )
    original_config = render.ssh_config
    homes = {device.id: peer.home for device, peer in zip(fleet.inventory.devices, peers)}
    monkeypatch.setattr(
        render,
        "ssh_config",
        lambda inventory, source: original_config(inventory, source).replace(
            "~/.ssh", str(homes[source.id] / ".ssh")
        ),
    )
    checks = fleet.sync()
    assert all(check.ok for check in checks), [(c.source, c.destination, c.detail) for c in checks]

    fleet.connector.batch = True
    before = fleet.path.read_bytes()
    checks = fleet.check()
    assert len(checks) == 6
    assert all(check.ok for check in checks)
    assert fleet.path.read_bytes() == before
