# Opt-in click and navigation log (M1)

## Why this increment exists

The owner reported successful sign-in followed by a return to the portal landing
page, with no timetable API request. Existing booleans show actions attempted,
but not their order or whether the portal redirected before/after OTTER's
schedule fallback. This increment adds observation only. It does not change the
navigation strategy or claim to fix the landing-page return.

## Enable and locate the log

From the same project/profile directory, run:

```sh
umask 077
set -o pipefail
./scripts/m1 fetch --headed --bootstrap-seconds 120 --navigation-log \
  | tee .m1-state/fetch-summary.json
```

Initially do not click/type. The one-line result includes `navigation_log` with
an exact filename, even on a normal bootstrap failure. Find that file inside
`.m1-state`, e.g. `navigation-<UTC timestamp>-<random suffix>.jsonl`. Each run
creates a unique mode-0600 file; existing logs are never overwritten. The state
lock and private directory still apply. `verify --navigation-log` puts both
browser lifetimes in one log. Logging is disabled unless requested.

The stdout summary and the JSONL navigation log are different files. JSONL has
one JSON object per line. The last line can be inspected in an editor, or the
whole small file can be attached for diagnosis. Share only these generated
reports, never Chromium's profile, HARs, console dumps, or storage exports.
Log files are ignored by Git and excluded from source patches.

## What the log records

Every row has sequence number, UTC timestamp, monotonic elapsed milliseconds,
and a fixed event name. Actions correspond to these exact configured selectors:

| Action | Intended control |
| --- | --- |
| student_entry_clicked | Visible level-two heading `Für Studierende` |
| sign_in_clicked | Button `Jetzt anmelden` |
| student_sso_clicked | Dedicated button `Anmeldung für Studierende und Lehrende` |
| my_studium_clicked | Unique visible exact text `My Studium` |
| timetable_link_clicked | Unique visible exact text `Stundenplan` |
| portal_start | OTTER's initial portal navigation |
| schedule_fallback | OTTER's direct timetable navigation after the stabilization delay |

`click_attempt` is written before the call; `click_completed` means Playwright's
click call returned successfully, not that SSO succeeded. `click_failed` contains
no exception text. Similarly, goto attempt/completed/failed rows separate
requested destinations from the resulting page URL. Browser main-frame requests,
responses (HTTP status only), frame navigations, failures, and observed SPA
locations are recorded separately. Iframes/subresources are excluded. Direct API
attempt/validation events indicate whether timetable retrieval was reached.

Example interpretation: `goto_attempt` with action `schedule_fallback` followed
by a navigation request to `/student/schedule` and a frame navigation to `/`
shows the return happened during/after that explicit navigation. It does not by
itself prove why the portal returned there or whether authentication was complete.
The event order should guide the next change, rather than another guessed delay.

## Redaction boundaries

No headers, request/response bodies, cookies, storage, page text/titles,
JavaScript error text, typed values, or actual identity information are recorded.
Only a fixed set of HTTPS origins and known routing paths is retained. Query and
fragment values are always removed, including OAuth state/codes. URL user-info
is never retained. Unknown origins are `other`; unrecognized paths are redacted
and represented by a short route hash so repeated unknown routes can be correlated.
Consequently this log intentionally cannot show every full URL. Do not replace it
with raw URL printing, Playwright DEBUG, a HAR, tracing, or TLS key logs.

Rows are flushed immediately. A failure or operator interruption closes the log;
a hard kill may leave a partial log. At most 2000 rows are written, with the last
row marking the cap. Logging errors fail the command instead of silently claiming
complete diagnostic evidence.

## Owner workflow: tests, commit, rollback

The assistant prepared/verified this patch in an isolated source copy, leaving the
owner's Git checkout and authentication state untouched. Apply it on the existing
M1 diagnostic branch after `git apply --check` succeeds. No new branch, push, or
production deployment is necessary.

Run in macOS Terminal, PyCharm's terminal, or Ubuntu:

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
git diff --check
```

Expect 60 tests and `OK`. New tests exercise redaction of query/fragment/path
secrets and user-info, private/unique files, flush/close on failure, schema
allowlisting, navigation filtering, URL deduplication, click outcomes, log size
limits, and CLI opt-in reporting. These tests do not prove live SSO behavior.
In PyCharm, use a Unittests configuration targeting `tests`, `.venv` as interpreter,
project root as working directory and `PYTHONPATH=src` (or mark `src` as Sources
Root). On Ubuntu, the same automated/headless commands apply; headed runs need
the existing temporary GUI/SSH tunnel procedure, not Nginx/systemd changes.

Before committing, review `git status` and `git diff`, stage only source/tests/docs
from this patch, run `scripts/check_public_tree.py`, then review
`git diff --cached --check` and `git diff --cached`. Include new module
`src/provadis_m1/navigation_log.py`, its tests and this document. Do not stage
patch downloads, logs, or `scripts/setup.save`. Suggested Conventional Commit:
`feat(auth): add redacted click and navigation logging`, with `Refs #N` in the
body (replace N with the actual issue number). This links the diagnostic work;
it should not close the unresolved authentication issue.

Before staging, reverse with `git apply --reverse --check <patch>` followed by
`git apply --reverse <patch>`. After committing, use `git revert <commit-id>`
and rerun the tests. Preserve user changes and profiles. Existing log files are
ignored data and will remain after a source rollback. Review the commit and
remote destination before any future push; deployment remains outside M1.
