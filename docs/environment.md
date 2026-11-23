# Environment, verification, and publishing

## Environment prepared here

The starting workspace was empty and was not a Git repository. A new isolated
repository was initialized in `provadis-timetable` on branch `main`.

The shell provided Git and Python 3.14, but no uv, Python 3.12 executable on PATH,
or GitHub CLI. A bundled Python **3.12.14** was subsequently located and used to
create this project's ignored `.venv`. Standard-library unit tests run under that
interpreter; this path is machine-specific and is not required by project code.

Shell network/DNS access is blocked in this execution session. A PyPI HTTPS
connection and SSH to `franz` both failed at hostname resolution. No server login,
production inspection, or configuration change occurred. No GitHub remote has
been published from this session.

A read-only check of GitHub's repository-creation page reached a sign-in form.
No GitHub session was available in that browser, and no repository was created.

The local connection subsequently dropped and was restored. After reconnecting,
all **31 unit tests passed under Python 3.12.14**, including the final portal
renewal handling. Python compilation, shell syntax checks, and Git whitespace
checks also passed. Shell access to PyPI still failed at hostname resolution;
reconnecting the local workspace did not change its network restrictions.

Playwright 1.62.0, greenlet 3.5.5, pyee 13.0.1, and typing-extensions 4.16.0 were
available in local package caches. An **outside-project temporary Python 3.14**
environment was used to check those cached packages. The available greenlet wheel
was for Python 3.14, so it was not installed into the Python 3.12 environment.
The offline Chromium smoke attempt did not successfully launch/operate a browser.
It is not evidence of either portal compatibility or incompatibility.

## Reproducibility boundary

`requirements.lock` pins the complete runtime dependency closure. `uv pip sync`
is the intended installation mechanism. This pip-compatible format was chosen
because uv and network resolution are unavailable here; no native `uv.lock` has
been fabricated. The environment is prepared, but dependency installation into
the Python 3.12 venv and real browser verification are still outstanding.

Remaining environment checks on a network-enabled terminal:

1. Install uv from its [official instructions](https://docs.astral.sh/uv/getting-started/installation/).
2. Run `./scripts/setup`, then the offline unit tests from the README.
3. Verify installation under Python 3.12 on macOS and Ubuntu 22.04. In particular,
   confirm compatible wheels for the pinned greenlet version and server architecture.
4. Prefer replacing the pip-compatible lock with a uv-generated `uv.lock` once
   resolution is available. Resolve all four current pins, review the diff,
   commit the generated lock, and switch setup/CI instructions together to
   `uv sync --locked`. Retain one authoritative runtime lock; do not leave two
   independently changing sets of pins.
5. Check official package hashes/distribution provenance when generating that
   native lock; the current lock has exact versions but no artifact hashes.

The version pins reflect the locally available packages, not a completed security
audit. Playwright installs a version-matched browser using its own installer;
that browser is outside Git. Do not change the browser independently without
rerunning authentication feasibility checks.

## Automated checks

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src scripts tests
git add README.md docs scripts src tests .github .gitignore
.venv/bin/python scripts/check_public_tree.py
git diff --cached --check
git diff --cached
```

The tests use wholly synthetic fixtures, request mocks, and private temporary
directories. No test requires a university account or network connection.
GitHub Actions runs these checks on Ubuntu 22.04 / Python 3.12.14 once published.
Its workflow does not receive credentials or try to authenticate.

The publication guard scans indexed file names and content, without printing
matches. It recognizes common private artifacts and token/key patterns; it is
not a comprehensive secret detector. Review all new/changed files before pushing.
If a credential was ever committed, removing it from the current tree is
insufficient: revoke it and remediate Git history before making it public.
The local repository uses `core.hooksPath=.githooks`; setup enables the same
pre-commit check in future clones. Hooks can be bypassed and do not replace review.

## New public GitHub repository

The intended suggested repository name is `provadis-timetable`. The local commits
are ready for review. If GitHub CLI is available and authenticated in your own
terminal, from the repository root:

```sh
python3 scripts/check_public_tree.py
# Uses the currently authenticated GitHub account; verify it first.
gh auth status
gh repo create provadis-timetable --public --source=. --remote=origin --push
```

If that name already exists, do not overwrite or attach to it blindly. Choose a
fresh name. If a new empty repository is created through GitHub's website instead,
use its exact SSH URL for `git remote add origin ...`, then `git push -u origin main`.
Never put a GitHub token in a URL, command argument, file, or chat.

Do not initialize the remote with an unrelated README or force-push over any
existing repository. No license choice has been assumed; choose one deliberately
before inviting external reuse.
