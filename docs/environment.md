# Environment, verification, and publishing

## Current state (24 November 2026)

The owner works in `/Users/Johannes/Downloads/otter` on
`fix/m1-session-diagnostics`; the configured repository is
`https://github.com/JJandke/otter`. No new repository or remote needs creating.
`LICENSE` contains GPL version 3; this cleanup does not change the license.

The declared interpreter target is Python 3.12, pinned to **3.12.14** in
`.python-version`. Read-only inspection of the owner's current `.venv/bin/python`
reported **3.14.7**. The successful local headless fetch/restart results do not
establish live Python 3.12 compatibility. They remain valid short-term evidence
for the current working environment.

The assistant tests isolated source copies with a separate Python 3.12.14
interpreter. Earlier notes describing unavailable tools, offline package caches
or unsuccessful browser launches describe the initial scratch environment, not
the current owner's working environment. They are not current acceptance results.
No server authentication or production configuration change has been performed
by the assistant.

## Preserve the ongoing longevity experiment

Do not recreate the current `.venv`, reinstall Chromium, rerun setup, switch OS
account, or move the browser profile while measuring session longevity. The setup
script now reads `.python-version` for both creation and validation, and exits 6
without replacing an incompatible existing venv. This is intentional.

Source patches and offline unit tests do not authenticate. They can be applied
without touching `.m1-state`. A fresh target-version checkout must have its own
venv and its own dedicated browser profile. Treat any interactive login or new
runtime/browser as a separate measurement series; do not silently combine it with
the original 72-hour evidence.

## Reproducibility boundary

The native `uv.lock` was generated with uv 0.13.9 using the public PyPI registry.
It pins Playwright 1.62.0, greenlet 3.5.5, pyee 13.0.1, and typing-extensions
4.16.0, retaining the prior runtime versions and adding distribution hashes.
The old `requirements.lock` has been removed. `uv sync --locked` is the installation
mechanism; the lock must match `pyproject.toml`. The virtual project is still run
with the existing `PYTHONPATH=src` wrapper, without adding packaging or a CLI API.

A fresh Python 3.12.14 environment was successfully synchronized in an isolated
macOS source copy; the 78 offline tests passed there. This is clean dependency
installation evidence, not university authentication evidence. The existing
Python 3.14 environment remains untouched. Chromium downloaded separately, but
its smoke launch exited before page validation in the assistant session. A local
browser smoke pass remains unproven; reproduce it with `scripts/check_browser.py`.

Remaining work:

1. Have the owner reproduce the fresh Python 3.12 installation and smoke checks
   in a separate checkout. Keep its profile separate from the longevity series.
2. Verify the updated GitHub Actions run on Ubuntu 22.04. It now installs the
   locked dependencies and Chromium, runs the offline suite and a sandboxed browser
   smoke check, and scans indexed files. It receives no university credentials.
   A workflow file in Git is not evidence of a passing GitHub-hosted job.
3. Repeat live authentication/restart tests in the target-version environment and
   on `franz`. A fresh install or browser smoke pass cannot establish login reuse.

Do not upgrade dependencies or browsers as part of this cleanup. The successful
working environment remains available until the replacement is verified. See
[m1-cleanup.md](m1-cleanup.md) for the current patch workflow and
[server-testing.md](server-testing.md) for the isolated Ubuntu experiment.

## Automated checks

From the repository root, in Terminal, PyCharm's terminal, or Ubuntu:

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src scripts tests
sh -n scripts/setup scripts/m1 .githooks/pre-commit
git diff --check
```

With the credential-assisted login feature expect **99 tests and OK**. The setup tests use temporary
synthetic checkouts, fake uv/Git/browser commands and the test interpreter. They
check that matching environments are reused, mismatches stop before installation,
new environments use the configured version, and unusable environments are
preserved. A failed locked sync stops before browser installation or Git changes.
No packages or browsers are installed by these tests. They run on the
current Python 3.14 environment too; this does not broaden the supported target.

The other tests use synthetic fixtures and mocks for API validation,
authentication/network error classification, navigation and state protection.
None needs university credentials or a network connection. PyCharm GUI setup is
explained in [development.md](development.md).

Before committing, stage explicit reviewed paths, then run:

```sh
.venv/bin/python scripts/check_public_tree.py
git diff --cached --check
git diff --cached
```

The guard scans indexed files; it is not a comprehensive secret or personal-data
scanner. Review the staged diff yourself. Setup enables `.githooks` via local Git
configuration; hooks do not replace review. Leave `scripts/setup.save`, browser
state, downloaded patches and diagnostic logs unstaged.

Before pushing, inspect `git status`, `git log --oneline main..HEAD`, and
`git remote -v`. Push the issue branch when ready, not a new repository. Merge to
`main` after review and the agreed acceptance gates; pushing and merging do not
constitute production deployment. See [feasibility.md](feasibility.md) for the
remaining M1 gates. Do not reset or clean away private state to change branches.
