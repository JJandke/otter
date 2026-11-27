# M1 cleanup: setup consistency and current acceptance evidence

This document records the preceding cleanup increment. For the subsequent
native-lock migration and current test count, see [uv-lock.md](uv-lock.md).

## Branch and scope

Continue `fix/m1-session-diagnostics`. The cleanup completes the same M1 work;
there is no need to return to `main` or create one branch per small patch. Separate
commits make the setup fix and documentation easy to review or revert. Merge the
branch after review and the agreed acceptance checks. No merge, push or production
deployment is performed automatically.

This incremental patch is based on `5207e8c`. The assistant prepared an isolated
copy and tested it without changing the owner's checkout, venv or browser state.
`requirements.lock`, browser versions and navigation behavior are unchanged.
Native uv locking and CI verification are subsequent increments.

## Important runtime finding

The owner's current `.venv/bin/python --version` reports **Python 3.14.7**. The
project declares Python **3.12.14**. The successful live results therefore do not
establish target-Python acceptance. The old setup check accepted 3.14.7 while its
error message said 3.12.14; the corrected script now follows `.python-version` for
validation, creation and the error message. It deliberately refuses a mismatched
existing venv instead of replacing it.

**Do not run setup on the ongoing longevity-test checkout.** Keep its interpreter,
browser and profile intact. Applying this patch and running offline tests do not
log in or replace that environment. Validate Python 3.12 later in a separate
checkout, with its own profile and separately recorded authentication series.
Do not copy the existing browser profile between checkouts or platforms.

## Apply and verify (owner actions)

Assuming the patch is saved in Downloads:

```sh
cd /Users/Johannes/Downloads/otter
git status --short --branch
git apply --check ~/Downloads/OTTER_M1_Cleanup.patch
git apply ~/Downloads/OTTER_M1_Cleanup.patch
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src scripts tests
sh -n scripts/setup scripts/m1 .githooks/pre-commit
git diff --check
```

Expect **77 tests, OK**. New tests exercise setup in temporary directories with
fake installer/browser/Git commands; no real dependency installation occurs.
A failure means stop before committing and inspect the failing test. These tests
cannot prove a real fresh install or live session longevity.

For PyCharm's GUI runner use Unittests targeting `tests`, the current `.venv`
interpreter, repository root as working directory, and `PYTHONPATH=src` (or mark
`src` as Sources Root). Ubuntu uses the same offline commands in its isolated M1
checkout after environment setup. This cleanup does not execute anything on
`franz` or change Nginx/systemd.

Review the updated feasibility table: local headless retrieval and short-term
restart reuse are demonstrated; live Python 3.12 compatibility, Ubuntu, long-term
renewal, idle recovery and a reauthentication drill remain open. Preserve private
summary/log evidence locally. Do not publish personal payloads or profile data.

## Two meaningful commits

Review `git diff`, then stage only the setup change and its regression tests:

```sh
git add scripts/setup tests/test_setup.py
.venv/bin/python scripts/check_public_tree.py
git diff --cached --check
git diff --cached
```

After reviewing that staged diff, commit (replace N with the actual issue number):

```sh
git commit -m "fix(setup): validate the interpreter against the project Python pin" \
  -m "Refs #N"
```

Then review and stage the documentation:

```sh
git add README.md docs/feasibility.md docs/environment.md \
  docs/authentication.md docs/development.md docs/m1-cleanup.md
.venv/bin/python scripts/check_public_tree.py
git diff --cached --check
git diff --cached
```

After reviewing it:

```sh
git commit -m "docs(m1): record authentication evidence and remaining runtime checks" \
  -m "Refs #N"
```

`Refs` links the issue without closing it. Leave `scripts/setup.save` and other
unrelated work unstaged. Before a later push, inspect both commits, check
`git status --short --branch` and `git remote -v`, and confirm checks passed. No
push is required to continue local longevity measurements.

## Recovery

Before staging, use `git apply --reverse --check <patch>` followed by
`git apply --reverse <patch>`. If staged, unstage only this patch's paths first
with `git restore --staged <paths>`; this keeps the edits. After committing, use
`git revert <commit-id>` for the commit to undo, then rerun the checks. No history
rewrite or profile deletion is needed. Do not use `git reset --hard` or
`git clean -fdx`.
