# Publishing

This is the setup for `BeepBoopBit/setup-ssh`. Once it's connected, publishing a
GitHub release runs the checks and uploads the package to PyPI.

## One-time setup

On PyPI, open [your account's Publishing page](https://pypi.org/manage/account/publishing/)
and add a pending GitHub publisher with:

| Field | Value |
| --- | --- |
| PyPI project name | `setup-ssh-cli` |
| Owner | `BeepBoopBit` |
| Repository name | `setup-ssh` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

The owner is the GitHub account that owns the repository. The workflow field
means the filename, `publish.yml`, not the full `.github/workflows/publish.yml`
path or its display name, “Check and publish.” Keep the environment name exactly
`pypi` in both places.

In the GitHub repository's **Settings → Environments**, create an environment
named `pypi`. No API-token secret is needed: the workflow uses
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

The first successful upload creates the PyPI project. Adding a pending publisher
doesn't reserve the package name. If you already own the project on PyPI, add
the publisher under that project's Publishing settings instead. PyPI explains
the first-upload flow in its
[pending publisher guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

## Make a release

1. Set the same version in `pyproject.toml` and
   `src/setup_ssh/__init__.py`. The first release is `0.1.0`.
2. Run `uv lock`, then `./dev.sh check`, `./dev.sh test`, and `./dev.sh build`.
   Use `.\dev.ps1` for those helper commands on Windows.
3. Commit and push the changes, including `uv.lock`.
4. On GitHub, open **Releases → Draft a new release**. Use a tag matching the
   version, such as `v0.1.0`, at the commit you want to release.
5. Add whatever release notes are useful, then click **Publish release**.
6. Watch **Actions → Check and publish**. Once the PyPI job succeeds, it's
   available to install.

The workflow checks formatting, runs the tests, checks that the versions and
release tag agree, builds the wheel and source archive, validates the package
metadata, and tries the wheel in a fresh environment. A separate job uploads
those same files to PyPI using the `pypi` environment.

Pushes to `main`, pull requests, and the manual **Run workflow** button run the
checks and build without publishing. A draft release or a pushed tag alone
doesn't upload anything. Publishing a prerelease also uploads to PyPI, so use a
prerelease version such as `0.2.0rc1` and tag `v0.2.0rc1` for those.

For the next release, choose a new version, such as `0.1.1`. PyPI won't let you
replace a file you've already uploaded. If a run failed before uploading, fix
the cause and rerun it. If it uploaded some files already, check the PyPI release
before retrying; the workflow deliberately reports duplicate uploads as errors.

## Install it elsewhere

```sh
uv tool install setup-ssh-cli
setup-ssh --help
```

To update an installation from PyPI:

```sh
uv tool upgrade setup-ssh-cli
```

With pip, use `python -m pip install --upgrade setup-ssh-cli` inside a virtual
environment. The package name is `setup-ssh-cli`; the command you run is
`setup-ssh`.
