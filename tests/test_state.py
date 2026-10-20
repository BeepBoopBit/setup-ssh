import json

import pytest

from setup_ssh.state import Device, Inventory, SetupError


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "bad\nHost *"},
        {"name": "-option"},
        {"host": "host; touch /tmp/oops"},
        {"host": "ssh://host"},
        {"host": "-oProxyCommand=bad"},
        {"user": "user\nProxyCommand bad"},
        {"user": 'a"b'},
        {"port": 0},
        {"port": True},
        {"port": 65536},
        {"platform": "unknown"},
        {"id": "../../elsewhere"},
        {"public_key": "ssh-ed25519 aGVsbG8="},
    ],
)
def test_reject_unsafe_inventory(changes):
    values = {"name": "linux", "host": "192.168.1.2", "user": "alex"}
    values.update(changes)
    with pytest.raises(SetupError):
        Device(**values).validate()


def test_inventory_round_trip_and_duplicates(tmp_path):
    inventory = Inventory()
    inventory.add(Device("linux", "100.100.10.2", "alex", local=True))
    path = tmp_path / "nested" / "inventory.json"
    inventory.save(path)
    loaded = Inventory.load(path)
    assert loaded == inventory
    assert "password" not in json.loads(path.read_text())
    with pytest.raises(SetupError, match="Duplicate"):
        inventory.add(Device("LINUX", "192.168.1.20", "someone"))
    assert len(inventory.devices) == 1
    with pytest.raises(SetupError, match="Only one"):
        inventory.add(Device("other", "192.168.1.30", "alex", local=True))
    assert len(inventory.devices) == 1


def test_malformed_inventory_is_not_overwritten(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text('{"devices": "broken"}')
    with pytest.raises(SetupError):
        Inventory.load(path)
    assert path.read_text() == '{"devices": "broken"}'
