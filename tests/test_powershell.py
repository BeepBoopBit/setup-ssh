"""Optional portable PowerShell smoke test; native Windows ACLs need Windows."""

import os
import shutil
import subprocess

import pytest

from setup_ssh.render import script
from setup_ssh.state import Device, Inventory, validate_key
from setup_ssh.transport import Session

PWSH = os.environ.get("SETUP_SSH_TEST_POWERSHELL") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(
    os.name == "nt" or not PWSH or not shutil.which("ssh-keygen"),
    reason="Requires portable PowerShell and POSIX OpenSSH",
)

# Replace only Windows-only ACL/membership calls. Everything else, including
# native keygen argument quoting and .NET atomic file replacement, is real.
DRIVER = r"""
$raw = [IO.File]::ReadAllText($env:SETUP_SSH_TEST_CODE)
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($raw, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$functions = $ast.FindAll({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -in @('Protect-UserPath','Authorized-Path')
}, $true)
foreach ($function in ($functions | Sort-Object { $_.Extent.StartOffset } -Descending)) {
    $replacement = if ($function.Name -eq 'Protect-UserPath') {
        'function Protect-UserPath([string]$Path) { }'
    } else {
        "function Authorized-Path { return Join-Path `$sshDir 'authorized_keys' }"
    }
    $raw = $raw.Substring(0, $function.Extent.StartOffset) + $replacement +
        $raw.Substring($function.Extent.EndOffset)
}
& ([ScriptBlock]::Create($raw))
"""


def test_streamed_script_exceeds_windows_command_limit(tmp_path, monkeypatch):
    (tmp_path / "powershell.exe").symlink_to(PWSH)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    session = Session(Device("windows", "192.0.2.5", "alex", platform="windows"))
    body = "# " + ("large payload " * 2000) + "\nWrite-Output 'Unicode: café ✓'\n"
    result = session.run(body)
    assert result.code == 0, result.stderr
    assert result.stdout.strip() == "Unicode: café ✓"


def test_powershell_keygen_repeat_apply_and_remove(tmp_path):
    home = tmp_path / "home with spaces"
    home.mkdir()
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ["ssh", "ssh-keygen"]:
        (commands / (name + ".exe")).symlink_to(shutil.which(name))
    runner = tmp_path / "driver.ps1"
    runner.write_text(DRIVER)
    body = tmp_path / "body.ps1"
    environment = dict(
        os.environ,
        USERPROFILE=str(home),
        ProgramData=str(home),
        PATH=str(commands) + os.pathsep + os.environ["PATH"],
        SETUP_SSH_TEST_CODE=str(body),
    )
    inventory = Inventory()
    device = Device("windows", "192.168.1.20", "alex", platform="windows")
    peer = Device("linux", "192.168.1.21", "alex", platform="linux")
    inventory.add(device)
    inventory.add(peer)

    def run(action):
        body.write_text(script(inventory, device, action))
        result = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(runner)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        return result.stdout

    public = validate_key(run("prepare").split("SETUP_SSH_KEY=")[1].strip())
    for item in inventory.devices:
        item.public_key = public
        item.host_key = {"type": "ssh-ed25519", "data": public.split()[1]}
    ssh = home / ".ssh"
    (ssh / "config").write_text("Host existing\n    HostName existing.example\n")
    (ssh / "authorized_keys").write_text("# existing authorization\n")
    private = ssh / "setup-ssh" / inventory.group_id / "id_ed25519"
    before = private.read_bytes()
    run("apply")
    config = (ssh / "config").read_text()
    authorization = (ssh / "authorized_keys").read_text()
    run("prepare")
    run("apply")
    assert config == (ssh / "config").read_text()
    assert authorization == (ssh / "authorized_keys").read_text()
    assert private.read_bytes() == before
    run("remove")
    assert (ssh / "authorized_keys").read_text() == "# existing authorization\n"
    assert (ssh / "config").read_text() == "Host existing\n    HostName existing.example\n"
