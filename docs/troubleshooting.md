# Troubleshooting

[Back to the README](../README.md) · [Usage guide](usage.md)

Start on the original setup computer:

```sh
setup-ssh list
setup-ssh plan
setup-ssh doctor
```

`list` and `plan` inspect saved state. `doctor` checks local tools, displays NetBird
status if installed, and prints SSH server setup instructions. None of these
commands proves that every remote SSH connection works.

## A computer is asleep or offline

Wake it and check its LAN/VPN connection. You can continue with available devices:

```sh
setup-ssh sync --skip-offline
```

Skipped computers keep their existing configuration. New authorizations reach
them on a later sync. Run `setup-ssh sync` with all computers online for a complete
group check. `setup-ssh check` has no option to skip offline devices.

## Connection timed out, refused, or no route to host

Check the saved address, SSH port, and username with `setup-ssh list`. On the
destination, confirm that the SSH server is running and the account is allowed
to log in. Check the host firewall and the network path in both directions.

For NetBird, confirm that both peers are connected and its policies permit the
selected SSH TCP port. Internet access alone does not mean the NetBird tunnel is
working. If the address is a hostname, confirm it resolves on every device.

A stale LAN address in an old `ssh mac` alias can differ from the saved inventory.
Inspect the effective SSH configuration without opening a connection:

```sh
ssh -G mac
```

Look at `hostname`, `user`, `port`, and `identityfile`. Use the actual enrolled
shortcut in place of `mac`. This version has one saved address per device and
does not automatically fall back between LAN and VPN addresses.

## NetBird's built-in SSH server

This tool configures the operating system's OpenSSH server over the NetBird VPN.
NetBird's built-in SSH server uses a separate authentication flow and can intercept
SSH traffic. If you enable that feature too, configure OS OpenSSH on a separate
port and enroll that port in setup-ssh. See [NetBird SSH](https://docs.netbird.io/manage/peers/ssh).

NetBird installation, enrollment, groups, and network policies are managed
separately; setup-ssh does not configure them.

## Setup still asks for a password

An existing shortcut does not by itself establish mutual key access. The initial
sync may need a password to install the managed public keys. If that sync stopped
before updating the destination, the next attempt can ask again.

Check that the setup computer is included in the inventory and that sync completed
for the destination. A `key collected` label only confirms that a public key was
saved. Complete the sync, then verify unattended operation:

```sh
setup-ssh --batch sync --skip-offline
```

Batch mode reports missing credentials or host trust instead of prompting. It
does not disable authentication. If the coordinator is not enrolled, it still
needs an existing key or another authentication method for management.

The CLI tries the local managed key, agent keys, and standard SSH key files. A
custom `IdentityFile` used by a manual SSH alias is not automatically imported.
An encrypted existing key can be made available through your SSH agent; managed
keys are generated without a passphrase.

For a detailed manual connection trace:

```sh
ssh -v -o BatchMode=yes mac
```

Review logs for local paths, hostnames, and account names before sharing them.

## Unknown or changed host key

An unknown host in batch mode needs an interactive sync or an already verified
entry in OpenSSH `known_hosts`. Compare the offered fingerprint with the target's
public host key before trusting it; see the [fingerprint commands](usage.md#first-connection-authentication).

For a **changed** host key, first confirm the destination and whether the computer
was reinstalled or its server keys were intentionally replaced. Verify the new
fingerprint on the target. Then remove and re-add the changed device, resolving
any incomplete revocation reported by removal. If OpenSSH also has a stale entry
for that address, update it only after verifying the replacement identity.

Setup pins server identities and does not automatically accept changed keys.

## Windows authentication or permissions fail

Use the account password, not a Windows Hello PIN. Confirm the username, OpenSSH
service, and firewall settings on the Windows computer. Local and Active Directory
accounts are supported by Windows OpenSSH; Microsoft Entra accounts are not
supported for its key authentication.

Administrator accounts normally use the shared file
`C:\ProgramData\ssh\administrators_authorized_keys`. Standard accounts use their
own `.ssh\authorized_keys`. When enrolling the local Windows computer as an
administrator, run setup from elevated PowerShell so it can update the shared
file and its ACLs. The scripts use Windows SIDs for administrator ACLs to support
localized group names.

Custom `AuthorizedKeysFile` locations and custom Windows `Match` rules are not
supported. Consult [Microsoft's configuration documentation](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-server-configuration)
and [key authentication guide](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement).

## Linux or macOS permission errors

Use the intended regular account. Confirm that it owns its `.ssh` directory and
can update the managed files. On macOS, Remote Login must allow that account.
Running setup as a different local user changes the home directory and credentials
being used; it is not a substitute for fixing the selected account's permissions.

Malformed managed blocks and symlinked managed files are rejected. Compare the
affected file with its `.setup-ssh.bak`, correct the reported problem while
preserving unrelated entries, and retry. Do not replace the entire `.ssh` directory.

## A managed key is missing or changed

Setup refuses to silently replace a key that peers already trust. Restore the
original private key from your own backup when possible. For replacement, follow
the [key recovery procedure](usage.md#changing-addresses-and-recovering-keys).

## Removal reports incomplete revocation

The device has already been removed from the inventory. Offline remaining peers
still have its old authorization until they are synced. Bring them online and run
sync. If the removed device itself was unreachable, its incoming authorization
blocks need local cleanup; subsequent group syncs no longer visit it.

## Sharing a useful bug report

Include `setup-ssh --version`, OS versions, the command and options, and the error
message. Say whether the failure is during enrollment, preparation, file updates,
or a particular connection direction. Redact account names and addresses where
appropriate. Never include passwords, private keys, tokens, or your full personal
inventory. For vulnerabilities, follow [SECURITY.md](../SECURITY.md).
