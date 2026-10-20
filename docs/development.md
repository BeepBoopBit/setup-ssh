# Dev commands

Just a few shortcuts so I don't have to remember the Python tooling commands.
You'll need Python 3.11+ and
[uv](https://docs.astral.sh/uv/getting-started/installation/) on PATH.

Use `./dev.sh` on Linux/macOS or `.\dev.ps1` in PowerShell on Windows. They share
the same commands. Running either one without arguments shows help.

| Command | What it does |
| --- | --- |
| `help` | Show the commands. |
| `doctor` | Check Python, uv, the SSH tools, and which `setup-ssh` is on PATH. |
| `install` | Install the current checkout as a standalone command for your user. |
| `reinstall` | Rebuild and install the current code, even if its version hasn't changed. |
| `uninstall` | Remove the CLI installed through uv. |
| `develop` | Prepare `.venv` with the dependencies from `uv.lock`, including dev tools. |
| `run ...` | Run this checkout's `setup-ssh` and pass along the arguments. |
| `check` | Check lint and formatting. Doesn't rewrite code. |
| `fmt` | Format the Python files. |
| `test ...` | Run pytest and pass along the arguments. |
| `build` | Put a wheel and source archive in `dist/`. |

For uploading a release to PyPI, see [Publishing](publishing.md). The GitHub
workflow handles that after you publish a release.

## Install and update

```sh
./dev.sh doctor
./dev.sh install
setup-ssh --help

# After pulling or making changes:
./dev.sh reinstall
```

The installed command has its own uv environment. You can use it outside the
checkout, and edits only reach it after reinstalling. `reinstall` also works if
you haven't installed it yet. This installation resolves dependencies from
`pyproject.toml`; the lockfile is for development.

If uv says its executable directory isn't on PATH, follow the path instructions
it prints and open a new terminal. No `sudo` is needed. The helpers don't install
Python or uv, or change your shell settings.

`uninstall` only removes the uv tool. Your saved devices, SSH keys, and SSH
configuration stay where they are. It doesn't revoke access between computers;
use `setup-ssh remove DEVICE` for that before uninstalling if you need to.
If you installed with pip instead, uninstall from that same Python environment.

## Work on the code

```sh
./dev.sh develop
./dev.sh run --help
./dev.sh test -q
./dev.sh test tests/test_state.py -k inventory
./dev.sh fmt
./dev.sh check
./dev.sh build
```

`run`, `test`, `fmt`, and `check` prepare `.venv` as needed, so `develop` is mostly
a convenient first step. These commands use `uv.lock` and fail if it's out of
date. After deliberately changing dependencies, run `uv lock`, then check in
both `pyproject.toml` and `uv.lock`.

You can call the helper by its path from another directory. Tests and code
checks run in the checkout. `run` keeps your current directory, so
`/path/to/setup-ssh/dev.sh run --config ./devices.json list` uses the file next
to you. `run` has the same effects as using `setup-ssh` directly, including
changing SSH settings when you ask it to sync.

`doctor` only reports what it finds. A missing installed CLI or NetBird is fine;
missing uv, SSH tools, or the lockfile returns a failure status.

If Python isn't found automatically, set `SETUP_SSH_PYTHON` to the path of a
Python 3.11+ executable. If Windows blocks the PowerShell script, you can use
`powershell -NoProfile -ExecutionPolicy Bypass -File .\dev.ps1 help` for a single
invocation without changing the saved execution policy.
