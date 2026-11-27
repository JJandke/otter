# M1: one bounded retry after the portal returns from the timetable

## Evidence and limits

The owner reported an unattended headless fetch with a validated snapshot of 80
items on 2026-11-24. The next `verify` failed during its first browser lifetime.
The local redacted log shows `Mein Studium` and `Stundenplan` clicks, followed by
`/student/schedule` at elapsed 8.137 seconds and a new main-document request for
`/` at 8.571 seconds. No timetable API request followed. The script stayed on `/`
until its original bootstrap timeout. Both menu actions were already marked used.

This establishes a route return and a missing recovery path. It does not establish
why the portal returned to `/`, nor prove that Microsoft authentication expired.
The successful fetch is evidence for headless profile reuse and unattended
retrieval on that occasion. Reliable repeated browser restart/verify and longer
session renewal remain unproven.

## Change

Observe main-frame route transitions independently of navigation logging. If
OTTER clicked `Stundenplan`, saw the exact timetable route, then finds the unique
visible `Mein Studium` card on the portal root again, permit one additional pair
of dashboard/timetable clicks. Do not retry merely because a click is pending or
because the address bar shows the timetable. Ignore child frames, OAuth callback
URLs, and foreign origins. The retry uses the existing bootstrap deadline, exact
selectors and ambiguity checks. No page reload, new SSO attempt, credential entry,
API retry, or relaxed response validation is added.

The summary adds `dashboard_retries` (0 or 1); an enabled navigation log adds the
fixed event `dashboard_retry` followed by the repeated click events. The menu
booleans describe the latest attempt after the reset; the retry counter preserves
evidence that recovery occurred. If the second attempt also returns to `/`, the
existing bootstrap failure remains a failure. An observed route alone is never
successful snapshot evidence.

## Apply and verify (owner commands)

This incremental patch is based on commit `bf1e813`. Stay on the current M1
issue branch so the investigation has one history. The assistant prepared and
tested an isolated copy; it did not edit the owner's checkout or profile, commit,
push, deploy, or change production configuration.

Assuming the patch was downloaded to `~/Downloads`:

```sh
cd /Users/Johannes/Downloads/otter
git status --short
git apply --check ~/Downloads/OTTER_M1_Dashboard_Retry.patch
git apply ~/Downloads/OTTER_M1_Dashboard_Retry.patch
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
git diff --check
```

Expect **73 tests, OK**. The new tests reproduce a brief timetable-to-root return,
check successful recovery without logging, reject ambiguous dashboard cards,
ignore callback/foreign/child-frame routes, and limit recovery to one retry.
These are synthetic offline tests, not a claim that the live portal is fixed.

For PyCharm's terminal use the same commands. For its GUI runner create a Python
Unittests configuration targeting `tests`, use the project `.venv` interpreter,
set working directory to the project root and `PYTHONPATH=src` (or mark `src` as a
Sources Root). Ubuntu uses the same offline commands from its isolated M1 checkout
after the documented environment setup. Do not copy a live macOS browser profile
to Ubuntu; server browser authentication/reuse needs its own controlled test.

Run serially without interacting with a browser:

```sh
umask 077
set -o pipefail
./scripts/m1 fetch --bootstrap-seconds 120 --navigation-log \
  | tee .m1-state/fetch-summary.json
./scripts/m1 verify --bootstrap-seconds 120 --navigation-log \
  | tee .m1-state/verify-summary.json
```

Run verify only after inspecting a successful fetch. Successful verify must show
`ok:true`, two `browser_runs`, at least two validated direct samples in each, and
`restart_reuse_demonstrated:true`. A retry counter of 0 is fine: the portal need
not bounce on every run. If it is 1, the navigation log should show the bounded
recovery. If failure remains, preserve the final summary and named navigation log;
do not delete the profile or guess further selectors. Counts may change from 80.

Before considering M1 complete, repeat serial fetch/verify runs and the existing
long-running session tests (including beyond the initial session lifetime), then
validate on isolated Ubuntu state. This patch adds no scheduler or production job.

## Review, commit, push, rollback

Review `git diff` before staging only these seven files:

```sh
git add src/provadis_m1/navigation.py src/provadis_m1/browser.py \
  src/provadis_m1/navigation_log.py tests/test_navigation.py \
  tests/test_browser.py tests/test_navigation_log.py docs/dashboard-retry.md
.venv/bin/python scripts/check_public_tree.py
git diff --cached --check
git diff --cached
```

Exclude `scripts/setup.save`, profiles, personal API responses, logs and downloaded
patches. After tests and review, use this Conventional Commit, replacing N with
the actual issue number (the milestone number is not the issue number):

```sh
git commit -m "fix(auth): retry timetable navigation after a dashboard return" \
  -m "Refs #N"
```

Use `Refs`, not `Closes`, until the issue's live acceptance criteria are met.
Before pushing review `git show --stat HEAD`, rerun the publication guard, and
check `git remote -v` and `git status`; then push the current issue branch when
ready. No production deployment is part of this increment.

Before staging, undo only this patch with `git apply --reverse --check <patch>`
then `git apply --reverse <patch>`. If already staged, unstage only the seven
listed files before reversing it. After committing, use `git revert <commit-id>`
and rerun tests. Preserve unrelated changes and the private authentication state;
do not use `git reset --hard` or `git clean`.
