import os
import shutil
import stat
import subprocess

import pytest

from setup_ssh.render import script
from setup_ssh.state import Device, Inventory, validate_key

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not shutil.which("ssh-keygen"), reason="Requires POSIX OpenSSH"
)


def execute(inventory, device, action, home):
    return subprocess.run(
        ["sh", "-s"],
        input=script(inventory, device, action),
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=15,
    )


def prepared(tmp_path):
    home = tmp_path / "home with spaces"
    home.mkdir()
    inventory = Inventory()
    device = Device("first", "127.0.0.1", "alex", platform="linux")
    other = Device("second", "127.0.0.2", "ACME\\alex", platform="linux")
    inventory.add(device)
    inventory.add(other)
    result = execute(inventory, device, "prepare", home)
    assert result.returncode == 0, result.stderr
    public = validate_key(result.stdout.split("SETUP_SSH_KEY=", 1)[1].strip())
    for member in inventory.devices:
        member.public_key = public
        member.host_key = {"type": "ssh-ed25519", "data": public.split()[1]}
    return home, inventory, device


def test_repeated_sync_preserves_keys_entries_and_permissions(tmp_path):
    home, inventory, device = prepared(tmp_path)
    ssh = home / ".ssh"
    (ssh / "config").write_text("Host personal\n    HostName existing.example\n")
    (ssh / "authorized_keys").write_text("# existing access\nssh-rsa abc existing\n")
    root = ssh / "setup-ssh" / inventory.group_id
    private = (root / "id_ed25519").read_bytes()
    for _ in range(2):
        result = execute(inventory, device, "apply", home)
        assert result.returncode == 0, result.stderr
        result = execute(inventory, device, "prepare", home)
        assert result.returncode == 0, result.stderr
    assert (root / "id_ed25519").read_bytes() == private
    assert (ssh / "config").read_text().count("# BEGIN setup-ssh") == 1
    assert "Host personal\n    HostName existing.example\n" in (ssh / "config").read_text()
    assert (
        (ssh / "authorized_keys")
        .read_text()
        .startswith("# existing access\nssh-rsa abc existing\n")
    )
    assert (ssh / "authorized_keys").read_text().count("setup-ssh:" + inventory.group_id) == 1
    assert (
        ssh / "config.setup-ssh.bak"
    ).read_text() == "Host personal\n    HostName existing.example\n"
    assert stat.S_IMODE((root / "id_ed25519").stat().st_mode) == 0o600
    assert stat.S_IMODE(ssh.stat().st_mode) == 0o700
    # Have OpenSSH itself parse spaces, quoted paths, domain usernames, and aliases.
    config = (root / "config").read_text().replace("~/.ssh", str(ssh))
    (root / "config").write_text(config)
    parsed = subprocess.run(
        ["ssh", "-G", "-F", str(root / "config"), "second"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "user ACME\\alex\n" in parsed
    assert f"identityfile {root}/id_ed25519\n" in parsed


def test_remove_preserves_unmanaged_access_and_private_key(tmp_path):
    home, inventory, device = prepared(tmp_path)
    ssh = home / ".ssh"
    (ssh / "authorized_keys").write_text("# keep me\n")
    assert execute(inventory, device, "apply", home).returncode == 0
    assert execute(inventory, device, "remove", home).returncode == 0
    assert (ssh / "authorized_keys").read_text() == "# keep me\n"
    assert "setup-ssh" not in (ssh / "config").read_text()
    assert (ssh / "setup-ssh" / inventory.group_id / "id_ed25519").is_file()


@pytest.mark.parametrize("file_name", ["config", "authorized_keys"])
def test_malformed_blocks_fail_without_replacing_target(tmp_path, file_name):
    home, inventory, device = prepared(tmp_path)
    path = home / ".ssh" / file_name
    value = f"# BEGIN setup-ssh {inventory.group_id}\nmissing end\n"
    path.write_text(value)
    result = execute(inventory, device, "apply", home)
    assert result.returncode != 0
    assert "Malformed" in result.stderr
    assert path.read_text() == value


def test_symlinks_are_not_followed_for_authorization(tmp_path):
    home, inventory, device = prepared(tmp_path)
    protected = tmp_path / "protected"
    protected.write_text("keep this\n")
    (home / ".ssh" / "authorized_keys").symlink_to(protected)
    result = execute(inventory, device, "apply", home)
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert protected.read_text() == "keep this\n"


def test_missing_private_key_does_not_silently_rotate(tmp_path):
    home, inventory, device = prepared(tmp_path)
    (home / ".ssh" / "setup-ssh" / inventory.group_id / "id_ed25519").unlink()
    result = execute(inventory, device, "prepare", home)
    assert result.returncode != 0
    assert "private key is missing" in result.stderr
