# Security

## Reporting a vulnerability

For a potential vulnerability, use the repository's private vulnerability-reporting
feature when available. If no private reporting channel is listed, open an issue
titled **Private security contact requested** to arrange one with the maintainer,
`beepboopbit`. Keep vulnerability details and exploit instructions out of that
public request.

A private report should include the affected version, operating system, impact,
and a minimal reproduction using disposable accounts. Do not send real passwords,
private keys, access tokens, or personal inventories. Ordinary configuration
problems can be reported publicly with sensitive details removed; see the
[troubleshooting guide](docs/troubleshooting.md).

## Access model

This tool grants mutual SSH access between all selected accounts. Each device has
a dedicated, unencrypted Ed25519 private key protected by local file permissions.
Someone able to read that private key can use its managed access until the key is
revoked on peers. Select accounts and protect devices accordingly.

Server host keys are verified and pinned. Account passwords are used only when
needed for initial authentication and are not saved. Private device keys are not
collected by the setup computer, and SSH-agent forwarding is disabled.

The public inventory contains network and account details, public keys, and host
pins. Although it contains no private keys or passwords, it should remain outside
public repositories and support attachments.

Windows administrators commonly share one authorized-key file. An authorization
there may permit other administrator accounts too; see the
[Windows troubleshooting guide](docs/troubleshooting.md#windows-authentication-or-permissions-fail)
for supported account layouts.

## Revocation and partial updates

Removing a device records removal intent before attempting changes. Offline peers
retain the old key until they can be updated. The removed device also retains its
incoming authorizations if it cannot be reached for cleanup. Uninstalling the CLI
does not remove previously granted access.

Updates are not transactional across computers. Successful changes remain if a
later operation fails. See [removal](docs/usage.md#remove-a-device) and
[recovery](docs/usage.md#changing-addresses-and-recovering-keys) for the supported
procedures.
