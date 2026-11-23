import asyncio
import io

import paramiko
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from setup_ssh.cli import main
from setup_ssh.state import Device, Inventory
from setup_ssh.status import inspect
from setup_ssh.transport import Connector, DeviceUnavailableError, Result


@pytest.fixture
def inventory():
    raw = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    )
    key = paramiko.Ed25519Key.from_private_key(io.StringIO(raw.decode()))
    devices = [
        Device(
            name,
            f"192.0.2.{index}",
            "alex",
            platform="linux",
            local=index == 1,
            public_key=f"{key.get_name()} {key.get_base64()}",
            host_key={"type": key.get_name(), "data": key.get_base64()},
        )
        for index, name in enumerate(("laptop", "desktop", "mac", "server"), 1)
    ]
    return Inventory(devices=devices)


@pytest.fixture
def probes(monkeypatch):
    closed = []

    class Source:
        def __init__(self, name):
            self.name = name

        def run(self, script, timeout):
            assert "Y2hlY2stYWxpYXM=" in script  # Encoded check-alias action.
            if self.name == "laptop" and "c2VydmVy" in script:
                return Result(255, "", "Connection timed out")
            return Result(0, "setup-ssh-ok\n", "")

        def close(self):
            closed.append(self.name)

    def open_source(connector, device):
        assert connector.batch
        if device.name == "server":
            raise DeviceUnavailableError("Source cannot be reached")
        return Source(device.name)

    monkeypatch.setattr(Connector, "open", open_source)
    return closed


def test_offline_source_keeps_all_directions_visible(inventory, probes):
    results = list(inspect(inventory, 1))
    directions = [result for result in results if result.destination is not None]
    assert len(directions) == 12
    assert sum(result.status == "OK" for result in directions) == 8
    assert sum(result.status == "Untested" for result in directions) == 3
    (failure,) = [result for result in directions if result.status == "Failed"]
    assert failure.detail == "Connection timed out"
    assert probes == ["laptop", "desktop", "mac"]


def test_dashboard_updates_matrix(tmp_path, inventory, probes):
    pytest.importorskip("textual")
    from textual.widgets import DataTable, Label

    from setup_ssh.dashboard import Dashboard

    path = tmp_path / "inventory.json"
    inventory.save(path)

    async def run():
        app = Dashboard(path, timeout=1)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            matrix = app.query_one("#matrix", DataTable)
            laptop, _, mac, server = inventory.devices
            assert matrix.get_cell(laptop.id, server.id).plain == "Failed"
            assert matrix.get_cell(server.id, mac.id).plain == "Untested"
            assert "8 passed" in str(app.query_one("#summary", Label).render())
        assert path.exists()

    before = path.read_bytes()
    asyncio.run(run())
    assert path.read_bytes() == before


def test_export_import_and_protect_management_inventory(tmp_path, inventory, monkeypatch):
    primary, portable, peer = [tmp_path / name for name in ("main.json", "fleet.json", "peer.json")]
    inventory.save(primary)
    monkeypatch.setattr("setup_ssh.cli.current_account_matches", lambda user: user == "alex")
    monkeypatch.setattr("setup_ssh.cli.local_platform", lambda: "linux")
    assert main(["--config", str(primary), "export", str(portable)]) == 0
    assert main(["--config", str(primary), "export", str(portable)]) == 1
    exported = Inventory.load(portable)
    assert exported.read_only and not any(device.local for device in exported.devices)
    command = ["import", str(portable), "--local", "mac"]
    assert main(["--batch", "--config", str(primary), *command]) == 1
    assert Inventory.load(primary) == inventory
    prefix = ["--batch", "--config", str(peer)]
    assert main([*prefix, *command]) == 0
    imported = Inventory.load(peer)
    assert imported.read_only and imported.group_id == inventory.group_id
    assert [device.name for device in imported.devices if device.local] == ["mac"]
    assert main([*prefix, "remove", "server"]) == 1
    assert main([*prefix, "sync"]) == 1
    assert Inventory.load(peer) == imported


def test_dashboard_can_open_before_enrollment(tmp_path, inventory, probes):
    pytest.importorskip("textual")
    from textual.widgets import DataTable, Label

    from setup_ssh.dashboard import Dashboard

    path = tmp_path / "inventory.json"

    async def run():
        app = Dashboard(path, timeout=1)
        async with app.run_test() as pilot:
            assert "No enrolled devices" in str(app.query_one("#summary", Label).render())
            assert "setup-ssh init" in str(app.query_one("#detail", Label).render())
            assert not path.exists()
            inventory.save(path)
            await pilot.press("r")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.query_one("#matrix", DataTable).row_count == 4

    asyncio.run(run())
