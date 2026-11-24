# M1: preserve the portal document after SSO routing

## Evidence from the owner's log

The navigation log from 24 November 2026 contains this ordering:

| Sequence | Elapsed | Event |
| --- | --- | --- |
| 46–49 | 6.88–7.00 s | SSO returns to `/` with a fragment; the fragment then clears |
| 50–57 | 7.01–8.34 s | Portal navigates to `/login/student`, then back to `/` |
| 62–66 | 10.43–10.56 s | OTTER requests `/student/schedule` using the full-document fallback |
| 68–69 | 10.89–10.96 s | Portal returns to `/` again |

Neither `My Studium` nor `Stundenplan` was clicked and no timetable API request
was observed. The first return to the landing page preceded the fallback; the
fallback therefore cannot explain the entire failure. These events establish
ordering, not successful authentication or the reason for a route guard rejecting
navigation. The root-cause investigation remains open.

The previous implementation never re-entered `Für Studierende` after SSO routing,
and its fallback made a full document reload instead of staying within the SPA.
This patch tests whether entering the student context again is needed. That is
an explicit hypothesis, not a claim that session validity has been established.

## Changed behavior

- Keep the original portal document alive after the SSO round trip: no `page.goto`
  timetable fallback, no longer delay guessed as an authentication signal.
- Prefer the reported `My Studium` / `Stundenplan` menu controls when visible.
- If the landing page remains and the student SSO option was selected, allow one
  additional click on the visible `Für Studierende` card heading. Log it separately
  as `post_sso_student_entry_clicked`.
- Never act on a portal URL while its OAuth query/fragment is present.
- Do not repeat `Jetzt anmelden` or the SSO-provider choice after re-entry. A
  returned login screen can still require manual reauthentication; no loop hides it.
- Log `selector_missing` once per expected action when it has zero visible matches.
  This provides absence evidence without publishing DOM text or screenshots.
- Retain the old `schedule_navigation_attempted` diagnostic for compatibility;
  it is now always false in a fresh run. Old fallback event names remain readable.

There is no credential entry, MFA automation, token export, calendar write, or
production change. If re-entry fails, inspect the new log before guessing more
selectors or treating a successful SSO routing click as proof of API access.

## Owner verification and Git workflow

The assistant prepared this incremental patch in an isolated tracked-source copy
of the owner's checkout, leaving the checkout and private profile untouched.
Stay on `fix/m1-session-diagnostics`. Check/apply the patch, then run from the
repository root (macOS Terminal, PyCharm terminal, or Ubuntu):

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
git diff --check
```

Expect 65 tests and `OK`. The five new regressions cover protected callback
handling, menu precedence, one post-SSO re-entry without any document navigation,
no repeated sign-in loop, one missing-selector event per action, and ambiguous
post-SSO headings. Two older fallback tests now enforce the absence of document
reloads. These mock-based checks cannot establish live SSO success. For PyCharm's
GUI test runner, select Unittests targeting `tests`, project root as working
directory, `.venv` as interpreter and `PYTHONPATH=src` (or mark `src` as Sources Root).

Then record a headed run without touching the browser:

```sh
umask 077
set -o pipefail
./scripts/m1 fetch --headed --bootstrap-seconds 120 --navigation-log \
  | tee .m1-state/fetch-summary.json
```

Share that safe summary and the unique JSONL filename named by `navigation_log`.
If headed retrieval succeeds untouched, test `fetch` headlessly, then `verify`
with `--navigation-log`. If a login/MFA prompt appears, use interactive `login`
yourself and retry. On Ubuntu, headed testing needs the existing temporary
GUI/SSH tunnel procedure; do not edit Nginx or systemd. Even a successful local
verify still leaves target-server and long-running renewal acceptance open.

Before committing: inspect `git status` and `git diff`; stage only the intended
source/tests/docs, including this new document; run `scripts/check_public_tree.py`,
then review `git diff --cached --check` and `git diff --cached`. Exclude local patch
files and `scripts/setup.save`. Suggested message:
`fix(auth): preserve portal state when re-entering the student area`, with
`Refs #N` in the body (replace N with the issue number). Keep the issue open until
its live acceptance criteria pass. No push or deployment is included.

Before staging, rollback with `git apply --reverse --check <patch>` and then
`git apply --reverse <patch>`. After committing, use `git revert <commit-id>` and
rerun tests, preserving other user changes. Never delete profiles or use a hard
reset/clean as a code rollback.
