# M1: native uv lock and clean environment verification

## What changed

Continue `fix/m1-session-diagnostics`. This incremental patch is based on
`9b682eb`. It replaces `requirements.lock` with an actual uv-generated `uv.lock`,
switches setup to `uv sync --locked`, and updates CI and current installation docs
together. No dependency upgrade is included: Playwright 1.62.0, greenlet 3.5.5,
pyee 13.0.1 and typing-extensions 4.16.0 are unchanged.

`pyproject.toml` declares the dependency intent, while `uv.lock` records the exact
resolved packages, registry sources and distribution hashes. Keep both in Git.
Do not edit lock hashes manually. `--locked` refuses stale dependency metadata
instead of silently changing the lock. uv 0.13.9 was used to generate and verify
this lock; CI pins that version. Routine `uv lock` retains compatible locked
versions; intentional upgrades need separate review and authentication checks.

The assistant generated the lock from public PyPI, synchronized a fresh Python
3.12.14 environment and ran the offline suite in an isolated macOS copy. This
proves installation on that machine, not the owner's authenticated Python 3.12
session or Ubuntu behavior. Chromium downloaded into an isolated directory, but
the process exited before the local smoke page could load in the assistant
session. The owner must run the smoke check below; no local browser pass is claimed.
The lock was also checked to reject changed dependency metadata without rewriting
it. No saved authenticated profile was inspected or copied. The current
Python 3.14 working environment, browser installation and longevity series were
not changed.

## Apply and verify in the existing checkout

Assuming the patch was downloaded to Downloads:

```sh
cd /Users/Johannes/Downloads/otter
git status --short --branch
git apply --check ~/Downloads/OTTER_M1_Uv_Lock.patch
git apply ~/Downloads/OTTER_M1_Uv_Lock.patch
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
sh -n scripts/setup scripts/m1 .githooks/pre-commit
git diff --check
```

Expect **78 tests and OK**. The new regression checks that a failed locked sync
cannot proceed to browser installation or hook configuration. Setup tests use
fake commands in temporary directories; they do not install anything. Existing
API/schema, error-classification, navigation and private-state tests remain.

Use the same commands in PyCharm's terminal. Its GUI runner uses Unittests,
`tests` as target, the current `.venv` interpreter, the repository root as working
directory and `PYTHONPATH=src` (or `src` as Sources Root).

**Do not run setup, `uv sync` or `uv run` in the current Python 3.14 checkout.**
Those commands manage the environment; keep the existing runtime during the
longevity experiment. Applying this source patch does not replace it, and
`scripts/m1` continues to use that existing interpreter.

## Commit and reproduce a fresh installation

Review `git diff`, then stage this patch's explicit paths:

```sh
git add uv.lock requirements.lock scripts/setup scripts/check_browser.py \
  scripts/check_public_tree.py \
  tests/test_setup.py .github/workflows/tests.yml .gitignore README.md \
  docs/environment.md docs/feasibility.md docs/server-testing.md \
  docs/m1-cleanup.md docs/uv-lock.md
.venv/bin/python scripts/check_public_tree.py
git diff --cached --check
git diff --cached
```

Staging `requirements.lock` records its deletion; it does not preserve a second
runtime lock. Leave `scripts/setup.save` and private state unstaged. Then commit,
replacing N with the actual issue number:

```sh
git commit -m "build(deps): adopt a native uv lock and verify clean environments" \
  -m "Refs #N"
```

For the clean check, clone the committed local source into a new path. A normal
clone copies committed source, not the working venv or ignored browser profile.
Check that the destination does not already exist; choose another fresh path if
needed. Use a separate terminal for this experiment:

```sh
git clone --branch fix/m1-session-diagnostics /Users/Johannes/Downloads/otter \
  /Users/Johannes/Downloads/otter-m1-python312
cd /Users/Johannes/Downloads/otter-m1-python312
# Keep the newly downloaded browser separate from the ongoing experiment.
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.m1-browsers"
./scripts/setup
.venv/bin/python --version
uv lock --check
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_browser.py
```

Expect Python **3.12.14**, 78 tests with `OK`, and a browser smoke result containing
`ok:true` and `sandbox_enabled:true`. The smoke script launches a temporary
sandboxed Chromium profile and checks an in-memory page. It never visits the
portal or API, and deletes its temporary profile on exit. It does not prove
university login or API access. Its failure exits 6 with a fixed reason; inspect
local browser dependencies without disabling the sandbox.

This local clone's `origin` points to the local source directory; use it as an
experiment, not as the checkout from which to publish changes. No login is
required for these clean-install checks. If later testing live authentication
there, use its own initial interactive login and record a separate series. Do not
copy the old profile. Keep the browser-path environment variable in that same
terminal for install, smoke, login and fetch commands.

## CI, Ubuntu, and remaining evidence

After reviewing the commit/remote and publication guard, push the issue branch
when ready. In GitHub Actions, inspect **M1 locked environment and checks** for
that exact commit. The job installs the locked environment and Chromium/Linux
libraries on an ephemeral Ubuntu 22.04 runner, then performs the offline suite,
sandbox smoke and publication guard. It gets no university credentials, cannot
verify a university session, and deploys nothing. Action revisions and uv are
pinned with readable version comments. Do not call CI verified until the run is
green; an unavailable runner/Python/action or failed download is still a failed
check requiring investigation.

On `franz`, use the isolated checkout and commands in `server-testing.md`.
Installing browser OS libraries may need sudo; production services/configuration
are not involved. A green hosted Ubuntu job is useful compatibility evidence but
cannot replace actual server GUI login and headless restart tests.

## Rollback

Before staging, check and apply `git apply --reverse <patch>` (use
`--reverse --check` first). If staged, unstage only these paths before reversing.
After committing, use `git revert <commit-id>` and rerun offline checks. This
restores the old requirements lock in source; it does not undo an environment
installation. Keep the original longevity checkout untouched and retire a failed
experimental checkout deliberately. Never delete or reset the original profile
as a dependency-installation recovery step. Avoid `git reset --hard` and
`git clean -fdx`.

References: [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/),
[uv in GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/).
