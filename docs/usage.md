# Usage guide

[Back to the README](../README.md)

Run management commands on the computer and account that created the inventory.
Other enrolled devices use normal `ssh`, `scp`, and `sftp` commands after setup.

## Initial enrollment

```sh
setup-ssh init
```

`init` is for an empty inventory. It asks whether to include the current computer,
then collects remote devices until you choose to stop adding them. For remote
devices, it asks for the OS, shortcut name, address, username, and SSH port, in that
order. For the current computer, its OS and account are detected; use its address
that the other devices can reach. With two or more devices, the wizard runs sync.

Including the setup computer makes its dedicated key available for future
passwordless management. Without it, that computer needs some other working
credential, such as an existing key, agent key, or an account password.

Enrollment is saved before setup. If setup fails, fix the problem and rerun
`setup-ssh sync`; do not re-add the same device.

## Add a computer

When every device is available:

```sh
setup-ssh add
```

When some devices are offline:

```sh
setup-ssh add --no-sync
setup-ssh sync --skip-offline
```

`add` has these options. Values omitted in interactive mode are prompted for.

| Option | Meaning |
| --- | --- |
| `--name NAME` | SSH shortcut; 1–63 letters, digits, underscores, or hyphens, starting with a letter or digit. |
| `--host ADDRESS` | LAN/NetBird IP or hostname, without a username, URL, or port. |
| `--user USER` | Local OS account used for SSH access. |
| `--port PORT` | SSH port, default `22`. |
| `--os OS` | `auto`, `linux`, `macos`, or `windows`; default `auto` for remote devices. |
| `--local` | Enroll this computer using its current account. |
| `--no-sync` | Save enrollment without connecting or configuring SSH. |

Names are compared without regard to case: `mac` and `Mac` conflict. Use names
that do not conflict with your existing SSH aliases: the managed configuration is
included first. Only one inventory entry can represent the local computer/account.

For scripted enrollment, provide the required name, host, and user explicitly:

```sh
setup-ssh --batch add --local --name linux --host 192.168.1.10 --user alex --no-sync
setup-ssh --batch add --name mac --host 192.168.1.20 --user alex --os macos --no-sync
setup-ssh --batch add --name windows --host 192.168.1.30 --user alex --os windows --no-sync
setup-ssh plan
setup-ssh sync
```

These addresses and usernames are examples; replace them with your own. Keep the
initial sync interactive unless both key authentication and host trust already
work for every required connection.

## First-connection authentication

Setup verifies the destination's host key before sending authentication
credentials. It uses a saved inventory pin, or a matching key already present for
that address in the setup account's OpenSSH `known_hosts`. Otherwise, it displays
a fingerprint and asks whether to trust it.

On the target Linux or macOS computer, display an Ed25519 host-key fingerprint:

```sh
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

On the target Windows computer, run in PowerShell:

```powershell
ssh-keygen -lf "$env:ProgramData\ssh\ssh_host_ed25519_key.pub"
```

Compare the fingerprint for the algorithm shown by setup; a server may offer a
different host-key type. Host keys identify the server and are separate from the
per-account keys this tool generates. Use the `.pub` files for this check.

For account authentication, setup tries its local managed key, SSH-agent keys, and
standard local key files before prompting for an account password. It does not
import arbitrary `IdentityFile` settings from existing SSH aliases. A working
manual alias can therefore use a credential that setup does not automatically try.

An initial password is only needed if available keys do not already grant access.
Passwords are not saved. Once an included setup computer successfully syncs, later
syncs can authenticate with its dedicated key. Verification commands use each
source's own key with password fallback and agents disabled.

## Sync and offline devices

```sh
setup-ssh sync
setup-ssh sync --skip-offline
setup-ssh sync --no-check
setup-ssh sync --dry-run
```

By default, sync connects to the complete group before generating keys. It
collects all public keys before changing authorization files, then updates SSH
shortcuts, host-key pins, and authorized keys. Finally, it checks every connection
direction by executing a harmless `echo` on the destination.

`--skip-offline` skips devices whose SSH port cannot be reached. It retains their
inventory entries and uses their saved keys and aliases where available. An
offline device without initialized keys is deferred. Authentication failures,
host-key mismatches, and configuration errors still fail the run.

Only available devices are updated and checked in this mode. A result such as
`2/2 SSH connections passed` for a three-device inventory means the two available
devices passed; it does not certify the offline device. If just one device is
available, it can be updated, but no inter-device connection can be checked. If
none are reachable, the command fails.

Offline computers receive new peers' keys on a later sync when they are online.
There is no background scheduler or automatic retry service. Run a full sync with
all devices online to verify the whole group.

`--no-check` skips connection tests; it does not make an unavailable device
reachable. It can be combined with `--skip-offline`. `--dry-run` only shows the
saved plan and does not connect, inspect live files, or predict reachability.

If an update fails partway through, successful updates remain in place. Setup
does not roll them back. Fix the reported problem and rerun sync.

## Inspect and test

```sh
setup-ssh list
setup-ssh plan
setup-ssh check
```

`list` shows the saved inventory and its location. `key collected` means a public
key is saved, not that every device has received it or is currently online.

`plan` previews the intended group, key path, and number of connection directions.

`check` requires completed setup and every device online. It does not rewrite SSH
configuration and has no `--skip-offline` option. To update and test the available
subset, use `sync --skip-offline`.

## Global options and exit codes

Global options go **before** the subcommand:

```sh
setup-ssh --batch sync --skip-offline
setup-ssh --timeout 5 check
setup-ssh --config ./my-devices.json list
setup-ssh --version
setup-ssh --help
setup-ssh sync --help
```

| Option | Meaning |
| --- | --- |
| `--batch` | Never prompt for passwords or host trust. Missing credentials or untrusted hosts cause an error. |
| `--timeout SECONDS` | Connection timeout, 1–300 seconds; default `10`. Not an overall run deadline. |
| `--config PATH` | Use a specific public inventory file. |
| `--version` | Print the package version. |
| `--help` | Show help; also available after each subcommand. |

`init` is interactive and does not support batch mode. Use explicit `add` options
with `--no-sync` for scripted enrollment.

| Exit code | Meaning |
| --- | --- |
| `0` | The requested operation succeeded. A partial sync may have deferred devices, as reported in its output. |
| `1` | Setup error, failed connection check, incomplete revocation, or ended interactive input. |
| `2` | Invalid command-line arguments. |
| `130` | Interrupted with Ctrl+C; saved enrollment remains. |

## Remove a device

```sh
setup-ssh remove desktop
```

Removal intent is saved immediately. Setup then attempts to remove the device's
managed key and shortcut from the remaining devices. If any are offline,
revocation is incomplete and the command exits nonzero. Run sync when those
devices return.

Setup also attempts to clean its managed entries from the removed device. If that
device is unreachable, it retains its incoming authorizations. Because it is no
longer enrolled, later group syncs do not clean that device: clean its marked
entries locally when you regain access. Managed private key files are retained.
Keys authorized outside this tool's marked blocks are unaffected.

Uninstalling the Python package does not revoke SSH access. Remove enrolled
devices and resolve any incomplete revocations before retiring the inventory.

## Files and backups

The default inventory location is:

| Platform | Path |
| --- | --- |
| Linux / macOS | `$XDG_CONFIG_HOME/setup-ssh/inventory.json`, or `~/.config/setup-ssh/inventory.json` when unset |
| Windows | `%APPDATA%\setup-ssh\inventory.json` |

Set `SETUP_SSH_CONFIG` or pass `--config` to override it. The inventory contains
addresses, usernames, device identifiers, public keys, and verified host keys.
It contains no account passwords or private keys, but can reveal network details.
Keep personal inventories out of public repositories and support reports.

On each device, the selected account owns:

```text
~/.ssh/setup-ssh/<group-id>/
  id_ed25519       dedicated private key; stays on this device
  id_ed25519.pub   public key
  config          SSH shortcuts for peers
  known_hosts     verified group host keys
```

`~` means the account's home directory, including its user profile on Windows.
Setup merges a marked block into `~/.ssh/authorized_keys` and prepends a marked
`Include` to `~/.ssh/config`. Windows administrator accounts normally use the
shared `%ProgramData%\ssh\administrators_authorized_keys` instead.

Unrelated entries are retained. When a merged file changes, its previous content
is saved as `<filename>.setup-ssh.bak`; this holds only the most recent pre-update
version. Generated files inside the group directory are rewritten as needed and
do not have that backup guarantee. Private keys are reused. Malformed marked
blocks and symlinked managed files are rejected.

## Changing addresses and recovering keys

Use `setup-ssh list` to find the inventory. Update a device's address or other
public connection details there, then run sync. Keep the group/device IDs intact.
The inventory's local-device flag is tied to the original setup computer and
account; copying it to a different computer does not transfer that role.

For a changed username, first arrange access and clean up the old account's
managed entries. Updating the inventory does not revoke keys in the old account.
For a reinstalled computer or changed host identity, verify the replacement
identity, then remove and re-add the device; resolve removal failures on peers.

If a managed private key is lost, restore it from your own backup. To replace an
orphaned keypair, remove the device, delete its managed `.pub` file if the private
key is missing, then re-enroll and sync. Setup deliberately refuses to silently
replace an orphaned or changed key.

See [troubleshooting](troubleshooting.md) for common failures and recovery steps.
