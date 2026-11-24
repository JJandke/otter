# OTTER development and M1 restart diagnosis

OTTER means **Organised Timetable Translation and Event Retrieval**.
Workflows in this guide keep each change small enough to understand and reverse.

## Current evidence

On 24 November 2026 the owner demonstrated headless fetch and two successful
headless verification lifetimes with two direct API samples each. Dashboard
recovery was exercised in both lifetimes. The current working venv is Python
3.14.7; clean Python 3.12 validation, long-duration renewal, and Ubuntu acceptance
remain open. See [feasibility.md](feasibility.md) and [m1-cleanup.md](m1-cleanup.md).

### Earlier diagnostic history

The owner reported these runs on 23 November 2026:

| Run | Result | Interpretation |
| --- | --- | --- |
| `login`, 14:44 CET | Validated 82 events; one direct API sample | Interactive authentication and direct retrieval worked in that run |
| `fetch`, 14:46 CET | No timetable Bearer observed | Restart bootstrap failed; reason not yet established |
| `fetch --portal-path '/#/student/schedule'`, 14:51 CET | Same failure | This route did not resolve the problem; its correctness is not established |
| `verify`, 14:53 CET | Same failure | Restart/unattended acceptance gate remains open |

These are owner-reported live results, not live runs performed by the assistant.
Two token-endpoint requests do not by themselves prove refresh-token renewal.

## Original diagnostic increment (historical)

- Prepared a patch against the owner's tracked `main` at `01b4b80`. The
  diagnostic source commit was developed in a separate writable checkout. The
  owner's repository has not been modified. Applying the patch changes files;
  it does not create a branch, commit, push, or deploy anything.
- Added safe diagnostics to successful runs and failures after browser launch.
  Only counts, booleans, HTTP status, and fixed location labels are returned.
  Actual URLs, titles, DOM content, storage, credentials, and JS errors are omitted.
- Missing timetable requests now use category `bootstrap`, exit **8**, because
  the lack of a request does not prove an expired session. HTTP 401/403 remain
  authentication failures, exit 2. Every such failure still has `ok: false`.
- Added `fetch --headed` to inspect the visible browser on the same profile.
  `verify --headed` is rejected: human-assisted navigation must not be presented
  as proof of unattended operation.
- Added `state_id`, a short hash of the resolved profile path. On the same host,
  the same path produces the same ID across runs. Different IDs indicate different
  paths; matching IDs do not prove valid authentication or identical profile content.
- Added regression tests and project instructions in `AGENTS.md`.

No profile contents were inspected, migrated, replaced, or exported. No push,
deployment, Nginx change, or systemd change is part of this increment.

## Commands you run: diagnose the existing session

Use the **same checkout and OS account** that produced the successful login.
The wrapper always uses `.m1-state` under its own project directory by default.
Opening another source archive creates a different project/profile path.

```sh
pwd
git status --short --branch
./scripts/m1 fetch --bootstrap-seconds 30
```

Ensure this working copy includes the new code before testing. If you run a copy
outside the workspace edited by the assistant, apply the code changes there;
never copy `.m1-state` between checkouts as a troubleshooting shortcut.

Then inspect one visible restart, retaining your existing profile:

```sh
./scripts/m1 fetch --headed --bootstrap-seconds 120
```

Initially **do not click or sign in**. Observe whether it shows the timetable,
a portal dashboard, or a login page. The browser closes at success or timeout.
You may deliberately navigate to the timetable as a separate manual diagnostic,
but record that you interacted; this does not prove unattended retrieval. Do not
share screenshots, account details, full identity-provider URLs, or browser storage.
Share only the generated JSON and a plain description of which kind of page appeared.

| Diagnostic | Interpretation / next step |
| --- | --- |
| `state_id` differs between runs on the same host | Check project directory and `--state-dir`; these runs used different profile paths |
| `final_location: identity_provider` | Inspect locally for a sign-in/MFA/consent screen; an SSO redirect alone does not prove why it stopped |
| `final_location: portal`, zero timetable requests | Portal view may not request timetable automatically; establish its actual route/navigation |
| `page_error_count > 0` | Portal script failure is possible; do not publish console dumps containing private data |
| `portal_api_transport_failed: true` | The browser observed a timetable transport failure |
| `last_portal_api_status: 401` or `403` | API access was rejected; check login/permissions |
| HTTP 200 but `timetable_bearer_observed: false` | Header observation or authentication mechanism needs investigation |
| Headed succeeds untouched, headless fails | Browser-mode behavior needs investigation; do not assume session storage is the cause |

Once the cause is resolved, run `./scripts/m1 verify` headlessly again. Do not
reset profiles or add storage-export code merely because bootstrap failed.
Session storage is one possible mechanism that may not survive browser restart;
its use by this portal is **not established**.

## Tests you run locally

From the project root, in macOS Terminal, PyCharm's terminal, or Ubuntu:

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

These tests need only Python's standard library, not browser credentials. They
verify response validation, error classification, profile isolation, exact-endpoint
header capture, diagnostic redaction, browser cleanup after bootstrap failure,
and rejection of headed verification. `OK` with exit 0 means those automated
checks passed; it does **not** mean live authentication or session persistence works.
`FAIL`/`ERROR` means stop and read the failing test before committing/pushing.

In PyCharm: choose this project's `.venv/bin/python` interpreter, mark `src` as
Sources Root, and create a **Python tests → Unittests** run configuration targeting
the `tests` directory. Set the working directory to the project root and add
`PYTHONPATH=src` to that test configuration. Run the full directory. A particular
failure can then be rerun from its test gutter icon.

Ubuntu uses the same automated commands. Headed diagnostics on SSH-only Ubuntu
require the temporary display and SSH tunnel in `server-testing.md`. Do not run
them against production scheduling or change Nginx to provide GUI access.

## Git workflow, review, and rollback

A branch is a movable name for a sequence of commits. Continue the existing
`fix/m1-session-diagnostics` branch for the M1 cleanup. There is no need to switch
to `main` or create a branch per patch. A commit records a reviewed source snapshot
locally; a push publishes commits to GitHub. Neither deploys the application.
Merge back to `main` after the work and acceptance evidence have been reviewed;
use a pull request if helpful. For this cleanup's two commits and exact staged
paths, follow [m1-cleanup.md](m1-cleanup.md).

Before committing, run the tests above, then review only the intended changes:

```sh
git status --short --branch
git diff
# Stage specific source/documentation paths after reviewing them, not private state.
git add path/to/reviewed-file
.venv/bin/python scripts/check_public_tree.py
git diff --cached --check
git diff --cached
```

The staging area is the selection that the next commit records. The publication
guard scans staged/indexed files; it cannot recognize every secret, so also read
the staged diff. A suitable commit message is (add `Refs #N` as the commit body, replacing N
with the actual issue number; milestone 1 is not necessarily issue 1):

```sh
git commit -m "fix(auth): diagnose timetable bootstrap failures safely"
```

`fix` describes a correction; `(auth)` names the area. Existing commits are kept
as they are; do not rewrite history just to change their message style.

Before any later push: rerun relevant checks if code changed, confirm `git status`,
review `git log --oneline main..HEAD`, verify `git remote -v` points to the intended
repository, and confirm the source contains no private state. The inspected
remote is `https://github.com/JJandke/otter`; verify it again before pushing. **Do not push or deploy as part of these diagnostic commands.**

Recovery:
- To unstage a file without deleting edits: `git restore --staged path/to/file`.
- To return to the clean baseline after the diagnostic commit: `git switch main`.
  If Git reports conflicting uncommitted changes, preserve/review them first;
  do not force the switch or use `reset --hard`.
- To undo a committed change on the branch while preserving history:
  `git revert <diagnostic-commit-id>`. Review the resulting inverse change and
  rerun the tests. This creates a new commit rather than erasing history.
- Ignored browser profiles are not rolled back by switching branches. Keep them
  private and intact; never use `git clean -fdx`, which can delete profiles/venvs.

Deployment remains outside M1. Any later deployment needs a separate review of
live evidence, target paths, backups, and production rollback before approval.
