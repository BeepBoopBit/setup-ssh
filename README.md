# setup-ssh

I made this because my lazy ass got tired of setting up SSH keys and remembering
IP addresses whenever I wanted to use another computer.

It's mostly for my own Linux, Mac, and Windows machines. Add them, sync, and then
use shortcuts like `ssh mac` or `ssh linux`. It sets up passwordless access in
both directions between the accounts you add.

This project uses **AI-driven development**. I use AI tools to help write the code
and docs and work through fixes.

## Get it running

You'll need Python 3.11+ on the computer running setup. The other computers just
need OpenSSH enabled and a working network connection. LAN is fine; I use NetBird
too, but you need to set that up separately.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then get the
published package from PyPI:

```sh
uv tool install setup-ssh-cli
setup-ssh init
```

This works on Linux, macOS, and Windows. Update later with
`uv tool upgrade setup-ssh-cli`.

Or use `python -m pip install setup-ssh-cli` inside a virtual environment.

Working from a clone of this repository? Use `./dev.sh install` instead, or
`.\dev.ps1 install` on Windows. `setup-ssh init` is the part that sets up your
devices.

Include your current computer in the wizard. It'll ask for each device's name,
address, username, OS, and SSH port. Keep the devices online for this first setup.
Verify the host fingerprints when asked; you may need each account's password
once so setup can install the keys.

After a successful sync:

```sh
ssh mac                  # from Linux
ssh linux                # from the Mac
scp notes.txt mac:~/
```

Use whatever names you gave your devices. Each computer keeps its own private
key, and you don't need to run setup again just to SSH into something.

## Add another computer

Enable SSH on it, then run these from your original setup computer:

```sh
setup-ssh add --no-sync
setup-ssh sync --skip-offline
```

That sets up whichever computers are available. Run sync again when the others
are online so they get the new keys too. If everyone's online, `setup-ssh add`
does both steps for you.

## Commands I use

```sh
setup-ssh list                       # saved devices
setup-ssh sync --skip-offline        # update the ones that are online
setup-ssh --batch sync --skip-offline # same, with no password/trust prompts
setup-ssh check                      # test every direction; all devices online
setup-ssh remove desktop             # remove a device's managed access
setup-ssh doctor --os windows        # SSH setup instructions for an OS
setup-ssh --help
```

Removing a device needs to reach the other computers to revoke its access there.
If some are offline, sync them later.

## More details

The longer explanations live here:

- [Usage guide](https://github.com/BeepBoopBit/setup-ssh/blob/main/docs/usage.md) — all the options, files, backups, and how keys work.
- [Troubleshooting](https://github.com/BeepBoopBit/setup-ssh/blob/main/docs/troubleshooting.md) — when something inevitably refuses to connect.
- [Contributing](https://github.com/BeepBoopBit/setup-ssh/blob/main/CONTRIBUTING.md) — running it locally and working on the code.
- [Dev commands](https://github.com/BeepBoopBit/setup-ssh/blob/main/docs/development.md) — install, update, test, and build.
- [Publishing](https://github.com/BeepBoopBit/setup-ssh/blob/main/docs/publishing.md) — the PyPI setup and release steps.
- [Security](https://github.com/BeepBoopBit/setup-ssh/blob/main/SECURITY.md) — the access model and reporting issues.

[MIT](https://github.com/BeepBoopBit/setup-ssh/blob/main/LICENSE) · beepboopbit
