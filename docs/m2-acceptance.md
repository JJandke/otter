# M2 acceptance and operator guide — Issue #10

This is the current M2 entry point. Increment guides A–F remain implementation
history; their old test totals are not the current suite total. Schema stays **3**.
This increment changes tests, CI and documentation only: no application source,
authentication, transport retry, migration, dependency or browser configuration.

Patch base: `33812ce55437195ddb9d76affa8a91ad33150843` (committed increment F).
Prerequisites: Issues #4–#9, already included. The base's subject/body repeat
Issue #8, but its diff contains Issue #9's recovery work. Use the actual SHA;
no history amendment or new branch/worktree is needed.
[Issue #10](https://github.com/JJandke/otter/issues/10) is the existing acceptance
and operator-documentation Issue; its body was empty at inspection.

## Current behavior and boundaries

Use `./scripts/otter` from the M2 checkout. Its wrapper selects that checkout's
.venv/PYTHONPATH and project root. `PYTHONPATH=src .venv/bin/python -m otter`
is equivalent when already in that root. M1's commands remain available separately;
M1 fetch/verify/acceptance diagnostics do not create M2 observations.

Successful M1 retrieval hands an immutable normalized candidate to assessment
and atomic persistence. Browser cleanup/profile-lock release precede finalization.
Separate successful observations retain separate UUIDs even if one content object
is shared. A UUID retry cannot become another observation. Structural failure,
quarantine and storage failure cannot move the accepted pointer.

The data model retains title, UTC start/end microseconds, both original numeric
offsets and nullable building/room. Missing/null/blank location means None; no
Teams/Online/physical location is invented. Valid old source events, including
2023, remain valid. No historical cutoff or calendar filtering is applied.

At one candidate receipt instant, future counts include events with end > receipt
in both candidate and last accepted baseline. A decrease is held when BOTH
drop >= 5 and drop/baseline_future > 30%, configurable through
--minimum-future-drop/--future-drop-percent and frozen per observation. Completed
lectures disappearing naturally do not inflate this decrease. Large growth alone
is informational, including publication of a whole new semester. Empty results,
exact duplicate rows and clock regression remain suspicious; valid same-title
lectures at different times remain separate rows within a snapshot.

Assessment does not prove feed completeness: unknown scope/pagination or a small
silent omission can evade count checks. M2 makes no cancellation decision. M3
must consume eligible accepted live observations in order, not count distinct
content hashes. Imports/held/failed/interrupted observations are not sync evidence.
No permanent lecture UID, matching, cancellation counter, ICS, scheduling, Nginx
change, service deployment or M1 merge is part of this increment.

## Acceptance matrix

All file names below are under tests/. Synthetic fixtures contain no real API
response or credentials. File-backed tests use private disposable directories;
subprocess and SIGKILL tests never target an owner's running M1 process.

| Required case | Existing automated evidence | Execution / assertion |
|---|---|---|
| 1. Valid normalization | test_m2_candidates.py; test_m2_acceptance.py | Actual parser plus complete fetch/store/restore journey; exact retained fields |
| 2. Mandatory fields/envelope/count | test_validation.py; test_m2_candidates.py; test_m2_cli.py | Synthetic malformed envelopes, counts, timestamps and late invalid rows reject the whole snapshot |
| 3. Optional omissions | test_m2_candidates.py; acceptance semester journey | Omitted/null/blank locations accepted as None; private/unknown metadata dropped |
| 4. Timezones | test_m2_candidates.py; test_m2_storage.py | Offset/DST-fold/spring/fraction/pre-epoch cases; aware UTC ordering and offset round trip |
| 5. Legitimate vs suspicious empty | test_m2_assessment.py; test_m2_ingestion_review.py | Repeated empty remains held; explicit fresh/latest/baseline-checked review can accept once |
| 6. Equal successful observations | test_m2_ingestion.py; test_m2_cli.py; acceptance semester journey | Distinct UUIDs, shared content, separate durable observations |
| 7. Duplicate/same-title lectures | test_m2_candidates.py; test_m2_fingerprints.py; test_m2_assessment.py | Multiplicity retained, exact duplicates held, same title/different times not merged |
| 8. Persistence | test_m2_storage.py; test_m2_ingestion.py | Real SQLite constraints and atomic content/event/attempt/pointer writes |
| 9. Restart durability | test_m2_storage.py; test_m2_cli.py; test_m2_acceptance.py | Fresh Python processes read the same persisted UUIDs/accepted history |
| 10. Atomic rollback | test_m2_ingestion_recovery.py; test_m2_retention.py | Fail content/pointer/expiry writes; retain accepted pointer and complete prior payloads |
| 11. Idempotent retry | test_m2_ingestion.py; test_m2_import.py; acceptance expired-import journey | Exact retry stays one observation, including after restore/expiry; conflicting context/content rejected |
| 12. Locks/concurrent writes | test_m2_storage.py; test_m2_ingestion_recovery.py; test_m2_backups.py | Actual subprocess locks and external SQLite writer; bounded busy failure; no provider retry on DB failure |
| 13. Recovery | test_m2_migrations.py; test_m2_backups.py; acceptance pending-write journey | Killed writer/backup/restore; explicit interrupted-state recovery; version 1/2 upgrade, verified restore |
| 14. API failure preservation | test_m2_cli.py; test_m2_handoff.py; acceptance semester journey | 401/403/5xx/transport/malformed response cannot replace accepted data |
| 15. Actual M1 integration | test_m2_handoff.py; test_m2_adapter.py; test_m2_cli.py; test_m2_acceptance.py | Real CLI/adapter/browser_run/direct_fetch/parser with fake Playwright transport and real SQLite |
| 16. M1 regression | All non-M2 tests; test_auth_browser.py opt-in | Existing auth/retry/credential/CLI/privacy contracts and local Chromium fixtures |
| Semester changes | test_m2_assessment.py; acceptance semester journey | Raw total 18 -> 7 accepted after completion; 7 -> 50 accepted on publication; genuine future drop/empty held |
| Historical retention/privacy | test_m2_retention.py; test_m2_backups.py; test_m2_store_guard.py | Accepted history cannot be pruned; canaries absent from DB/backups/output; force-staged private data rejected |

The three new composed tests are in `test_m2_acceptance.M2AcceptanceTests`:

- test_semester_fetch_failures_restart_retention_and_restore_preserve_accepted_history
- test_expired_import_replay_after_restore_keeps_uuid_count_and_payload_expiry
- test_pending_live_write_survives_backup_restore_and_recovery_without_refetching_old_uuid

They join existing boundaries rather than adding another fetch/normalization
implementation. The first and third execute actual M1 pipeline code with the
existing HTML-backed fake transport; origin=live here is a logical test state,
not evidence of a university request or real Microsoft JavaScript. Fake-clock
injection is test-only. Existing real file-backed kill/concurrency tests remain
in full discovery; no new long-duration authentication test is introduced.

## Run the checks on both hosts

From macOS Terminal, PyCharm's terminal, or Ubuntu's isolated M2 checkout:

```sh
.venv/bin/python --version
.venv/bin/python -c 'import sqlite3; print(sqlite3.sqlite_version)'
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_m2_acceptance.py -v
OTTER_BROWSER_TESTS=0 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests scripts
sh -n scripts/setup scripts/m1 scripts/m1-acceptance scripts/otter .githooks/pre-commit
git diff --check
```

At this increment: three composed tests pass; full offline discovery is
**389 total, 381 passes, eight intentional Chromium skips**. Setting
OTTER_BROWSER_TESTS=0 keeps offline results independent of the shell's prior flag.
The full suite includes the three composed tests; running that file separately
is useful for focused diagnosis, not an additional acceptance requirement.

PyCharm: select M2's .venv/bin/python, M2 root as working directory, Unittests
target tests, environment PYTHONPATH=src and OTTER_BROWSER_TESTS=0. Use a separate
configuration with OTTER_BROWSER_TESTS=1 for the browser file only. Do not put
credentials into a shared IDE configuration or run ordinary tests against a
live profile/database.

Run local Chromium fixtures separately on both usable hosts:

```sh
OTTER_BROWSER_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover \
  -s tests -p test_auth_browser.py -v
```

Expected: eight passes, no skips. A launch error is a failed/unexecuted browser
check, not a passed fixture. If binaries are genuinely missing, install Chromium
for this environment outside ongoing M1 acceptance. Do not loosen sandbox flags
to hide a failure or casually run scripts/setup: hooks/cache/environment changes
can affect an active test. Ubuntu uses the same Python commands; no SQL server,
Docker or dependency update is needed. Record its actual Python-linked SQLite
version instead of assuming it matches macOS.

CI keeps the existing pinned actions, Ubuntu 22.04 runner, Chromium checks,
privacy guard and full test discovery. It now identifies M1+M2 coverage, prints
Python/SQLite versions and explicitly separates offline discovery from the browser
step. CI never logs in to Provadis/Microsoft, injects live credentials or uploads
runtime databases/profiles. Remote CI has not been executed by patch preparation;
check its result when an owner-approved push/PR eventually runs it.

## Database and operator checklist

The common entry points are in [m2-cli.md](m2-cli.md); verified backup, restore,
quarantine expiry, growth/privacy limits and crash recovery are in
[m2-recovery.md](m2-recovery.md). Use the latter's full stopped-writer runbook.

For an existing store, inspect before fetching:

```sh
./scripts/otter status --directory .otter-data
./scripts/otter check --directory .otter-data
./scripts/otter schema --directory .otter-data
```

Use its actual directory if it is outside the checkout. Schema 3 needs no new
migration for Issue #10. For an older known store, pause its writers/connections,
run check/backup, then explicitly migrate and check/status again. Only genuinely
new stores use `./scripts/otter init --directory .otter-data`. A missing/corrupt DB
is not a new empty history; preserve all files/journals and restore into a new
private directory. Never copy only a changing DB or delete a lock/journal to force
progress. Backup/restore APIs preserve the original stream/observation identities.

M2 runtime defaults are .otter-data and .otter-runtime/.m1-state; keep both private
and outside any public web root. A backup contains projected timetable data, not
the browser profile. Titles/locations can still be personal; keep databases,
backups, raw responses and profiles out of Git and shared diagnostic archives.
Status prints redacted metadata, not event rows. No tokens, cookies, passwords,
person IDs, emails, instructor/group/datasource or raw messages are stored.

Exit 0 means the requested operation completed. Fetch/import acceptance requires
durable accepted status; suspicious=5, invalid=4, storage=9, conflict/rejected
review=10. Other M1 failure categories retain their documented codes. Read-only
inspection returning 0 does not promote a suspicious observation. On failure,
preserve the prior accepted data and inspect fixed reason codes. Do not override
empty/quarantine merely to pass a check. Pending writes report a UUID, not a new
successful fetch; recover classifies abandoned starts under the store lock.

Quarantine expiry is explicit and defaults to preview; apply takes a verified
backup first. Accepted contents remain indefinitely. Expiry preserves observation
metadata but removes old quarantine payload rows, not source-date history. No
automatic backup rotation, secure-erasure promise or VACUUM is introduced.

## Dedicated live integration evidence

The owner reported all Issue #8 tests passing on both hosts after receiving the
dedicated fresh-profile/strict-wet ingestion protocol. This is recorded as
**owner-reported passing live integration**, not a run performed by preparation.
Issue #9/#10 do not change that auth/fetch pipeline. Reuse that evidence; do not
repeat real login or 25/26-hour tests solely for this acceptance/documentation patch.
If that report did not include the dedicated protocol, complete it before closing
the live gate. Commands below are for that missing check or deliberate reproduction,
not something preparation executes automatically.

From M2 on each host, use its existing owner-only credential file outside Git:

```sh
umask 077
m2_live_root="$(.venv/bin/python -c 'import tempfile; from pathlib import Path; print(Path(tempfile.mkdtemp(prefix="otter-m2-live-")).resolve())')"
credentials_file="$HOME/.config/otter/credentials.env"
./scripts/otter init --directory "$m2_live_root/data"
./scripts/otter fetch --directory "$m2_live_root/data" \
  --state-dir "$m2_live_root/.m1-state" \
  --credentials-file "$credentials_file" --decline-passkey --bootstrap-seconds 180
./scripts/otter status --directory "$m2_live_root/data"
./scripts/otter fetch --directory "$m2_live_root/data" \
  --state-dir "$m2_live_root/.m1-state" --bootstrap-seconds 180
./scripts/otter status --directory "$m2_live_root/data"
./scripts/otter check --directory "$m2_live_root/data"
```

These perform real sign-ins/API GETs when the owner deliberately runs them. Stop
on nonzero, don't retry bad credentials or reuse M1 acceptance/production profiles.
Each fetch must have one direct sample, accepted/live/eligible_for_sync=true,
and its own UUID. The second runs in a new process with no credential file and
must reuse the healthy session. Two accepted observations may share one content;
a real intervening timetable change may legitimately produce two contents.
The separate status processes demonstrate committed reads. Share only redacted
summaries, revision and runtime versions; never credentials/profile/raw response.

## Evidence and milestone closure

| Evidence | macOS | Ubuntu |
|---|---|---|
| A–F / Issues #4–#9 tests | Owner reports passed | Owner reports passed |
| Dedicated Issue #8 ingestion | Passed as owner-reported above; detailed runtime/JSON not supplied | Passed as owner-reported above; detailed runtime/JSON not supplied |
| Issue #10 preparation offline | 389 discovered, 381 passed, eight skips | Not run by preparation |
| Issue #10 preparation Chromium | Eight startup errors, no fixture assertions passed | Not run by preparation |
| Issue #10 final owner verification | Awaiting result for this patch | Awaiting result for this patch |

Preparation runtime: macOS arm64, Python 3.12.14, linked SQLite 3.53.1,
Playwright 1.62.0. Work's Chromium fails startup with TargetClosedError/SIGABRT.
No live auth, Ubuntu execution, remote CI, deployment or production change is
performed by preparation. Passing fake transport/SQLite tests cannot prove
Microsoft behavior or unattended reauthentication over session expiry.

For final owner results, retain a small redacted record per host: base+patch or
committed SHA, Python/SQLite versions, offline passed/skipped count, explicit
browser result, and reference to the already successful live check. No personal
payload/artifact upload is necessary. M2 can be accepted after this increment's
host checks pass and live evidence is confirmed. M1's independent long-duration
gate is not silently closed by M2; do not call the whole service deployed/ready.

## Apply, commit and reverse this increment

```sh
cd "$HOME/Downloads/otter-m2"
git status --short --branch
git rev-parse HEAD
patch_file="$HOME/Downloads/OTTER_M2_Issue_10_Acceptance.patch"
git apply --stat "$patch_file"
less "$patch_file"
git apply --check "$patch_file"
git apply "$patch_file"
git diff --check
```

Require clean milestone/m2-validation-storage at the exact base. Checking is
read-only; applying creates no commit. Never switch the original M1 checkout,
recreate its branch/worktree, or use --reject to partially install. Run the
checks above on both hosts. Patch preparation verifies exact tree/modes plus
forward/reverse application and full tests in disposable checkouts.

After review/testing, stage just the five changed paths:

```sh
git add -- .github/workflows/tests.yml README.md tests/test_m2_acceptance.py \
  docs/m2-acceptance.md docs/m2-recovery.md
git diff --cached --check
git diff --cached
.venv/bin/python scripts/check_public_tree.py
git commit -m "test(m2): verify persistence acceptance and document operations" -m "Refs #10"
```

Staging selects reviewed source-controlled files; the guard checks indexed
artifacts. The local commit records the increment; Refs #10 links progress
without closing it. Do not add private test/runtime data. No commit/push/Issue
edit/PR/merge occurs automatically. After owner confirmation, a later explicitly
approved PR can describe the completed M2 work and link Issues #4–#10.
Prefer a normal M1 merge commit into main, preserving the common ancestry, then
integrate M2 afterward; this patch does not merge either branch or presume M1
was already merged. Do not rewrite shared history to fix the old commit subject.

Before commit, with no overlapping edits: if the patch is already staged, first
unstage those five paths:

```sh
git restore --staged -- .github/workflows/tests.yml README.md tests/test_m2_acceptance.py \
  docs/m2-acceptance.md docs/m2-recovery.md
```

Then reverse it (start here if it was never staged):

```sh
git apply --reverse --check "$patch_file"
git apply --reverse "$patch_file"
```

Unstaging preserves files for reversal. This patch changes no schema or runtime
data; source reversal leaves version-3 stores/profiles intact and usable by the
Issue #9 base. After commit use a reviewed git revert. Do not use reset-hard or
git clean -fdx. After host verification, confirm M2 acceptance before planning M3;
no calendar generation or deployment is implied.
