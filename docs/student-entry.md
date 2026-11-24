# M1: enter the student portal before waiting for the timetable

The owner confirmed that opening `/student/schedule` without starting the student
flow lands back on `/`. Waiting longer there causes no timetable or identity
requests. Clicking `Für Studierende` manually allowed SSO without credentials and
a validated 82-event response. That is the observed basis for this patch.

## Automatic behavior

- Start at the public landing page `/`. After entry/SSO, use `/student/schedule`
  as the target, preserving `--portal-path` overrides.
- While waiting for the timetable Bearer, check only the exact HTTPS portal origin.
- On its plain landing page, click the unique visible level-two heading named
  `Für Studierende` at most once. Multiple matches fail closed; missing/hidden
  entries cause no guessed click. Public DOM inspection confirmed a hidden sidebar duplicate and a visible
  `<h2>` card heading. The selector now uses heading role, level 2, exact name,
  and visible filtering. Full live SSO still needs verification.
- Leave portal query/fragment callbacks alone. A later incremental patch adds
  the dedicated B2C student-provider choice; all credential/MFA prompts remain
  manual. See [SSO navigation](sso-navigation.md).
- If the portal is on a different `/student` page without a callback URL, wait
  two seconds, then attempt the target schedule URL once. This is a stabilization
  delay, not proof of completed authentication. Expiry/MFA can still require login.
- All navigation uses the existing bootstrap time budget. There is no repeated
  click or sign-in loop. Direct API validation remains mandatory for success.
- Summaries include `student_entry_clicked` and `schedule_navigation_attempted`.
  No URLs, credentials, page content, tokens, or errors are dumped.

This patch does not establish reliable authentication yet. It automates the
observed missing entry step so the next live test can establish whether that is
sufficient. Existing profile contents and OS key storage are not modified by any
custom authentication code; Chromium continues to manage its own state normally.

## Owner verification before committing

Use your current diagnostic branch. This is an incremental patch **after** the
bootstrap-diagnostics patch, prepared against commit `1d50219`. Your untracked
patch downloads and `scripts/setup.save` are not part of it.

After `git apply --check` succeeds and you apply the patch, run on macOS Terminal,
PyCharm's terminal, or Ubuntu, from the repository root:

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
git diff --check
```

The student-entry increment originally had 45 tests. With the SSO-navigation
increment, expect 52 tests and `OK`. The new tests check one bounded student-entry click,
no clicks on identity/callback URLs, rejection of ambiguous matches, one schedule
navigation after the delay, safe click errors, and the default route. They use
mocks and do not prove live SSO or DOM compatibility. For PyCharm's test runner,
use Unittests targeting `tests`, the project root as working directory, `.venv`
as interpreter, and `PYTHONPATH=src` (or `src` as Sources Root).

Then run without clicking or typing:

```sh
./scripts/m1 fetch --bootstrap-seconds 60
./scripts/m1 verify --bootstrap-seconds 60
```

Only run verify after fetch succeeds. Verify must show two successful headless
browser lifetimes. If fetch fails, use `fetch --headed --bootstrap-seconds 120`
and observe without intervening. Report the safe JSON and the kind of page shown.
On SSH-only Ubuntu, headed testing requires the existing temporary GUI/tunnel
procedure; do not change production Nginx or systemd. Long-term renewal tests
remain outstanding even after a successful verify.

After tests, inspect `git diff`, stage only the intended source, tests, and docs,
run `scripts/check_public_tree.py`, and review `git diff --cached --check` and
`git diff --cached`. A suitable local commit is
`fix(auth): enter the student portal before fetching the timetable`, with
`Refs #N` in the body (replace N with the actual issue number). Keep the issue
open until live headless fetch/verify and the issue's other requirements pass.
No push or deployment is performed by applying this patch.

Before staging, rollback with `git apply --reverse --check <patch>` followed by
`git apply --reverse <patch>`. After committing, use `git revert <commit-id>`
and rerun tests. Do not delete browser state or use a hard reset/clean to undo code.

## Saving the safe summary and tracking new files

The JSON summary is stdout; no report file is created automatically. On macOS
zsh/bash, save and display it with:

```sh
umask 077
set -o pipefail
./scripts/m1 fetch --bootstrap-seconds 60 | tee .m1-state/fetch-summary.json
```

The existing private state directory must exist (as it does after login). Only
stdout is saved; this command does not export browser state. Pipefail preserves
a nonzero fetch status even when tee succeeds. The report is ignored by Git.

The visible-heading correction is incremental after the student-entry patch.
Inspection found these files untracked in the owner's working tree:
`src/provadis_m1/navigation.py`, `tests/test_navigation.py`, and
`docs/student-entry.md`. Include them when staging the next commit; otherwise
a fresh checkout will miss the navigation module even if local tests pass.
Run the full tests, inspect the diff, stage the intended files, run the publication
guard, and review `git diff --cached` before committing. Do not stage patch downloads
or `scripts/setup.save`. Suggested message:
`fix(auth): select the visible student heading from the landing page`.
Use `Refs #N` with the actual issue number, keeping the issue open until live
headless fetch and verify succeed. The existing rollback instructions apply.
