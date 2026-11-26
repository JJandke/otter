# M2 increment E — M1 integration and CLI (Issue #8)

**Increment F update:** current schema is version 3. The commands and results
below describe increment E. See [backup, recovery and quarantine maintenance](m2-recovery.md)
for Issue #9's commands, explicit upgrade and current checks. Do not initialize
again to replace an existing store.

Base: `47b613fac23887444814e838f4d6fd736a072eab` (committed Issue #7).
Prerequisites: increments A–D, already in this base. This patch addresses
[Issue #8](https://github.com/JJandke/otter/issues/8) in the existing M2 milestone.
No original worktree/profile/database, GitHub Issue/PR, production service or
remote branch is modified by patch preparation.

## Entry points and scope

Run `PYTHONPATH=src .venv/bin/python -m otter ...` from the M2 checkout, or use
`./scripts/otter ...`. The executable wrapper resolves its own checkout, sets
PYTHONPATH, changes to that project root and uses its .venv interpreter. It does
not select the original M1 checkout. `python -m provadis_m1` and scripts/m1 remain
unchanged; their authentication/verify behavior and count-only output are preserved.

| Command | Behavior |
|---|---|
| `init` | Explicit private-store initialization, never overwrites history |
| `migrate` | Explicit checksummed upgrade with the existing verified backup mechanism |
| `status` | Counts and redacted last-accepted/last-successful metadata |
| `schema` | Static SQLite table/index/trigger definitions |
| `fetch` | One M1 browser lifetime/direct sample, then durable M2 ingestion |
| `import-json FILE --observation-id UUID` | Explicit offline import with a stable ID; never live sync evidence |
| `observation UUID` | Read-only observation summary and original review/baseline metadata |
| `review UUID` | Same read-only preview; approval requires explicit flags |
| `recover` | Lock-protected classification of abandoned starts as interrupted |

All commands accept `--directory PATH` **after the command**, default .otter-data.
No SQL server, Docker, ORM, package upgrade, scheduling, ICS, UID, matching or
cancellation logic is introduced. Schema remains **version 2**: no new migration.
General backup/restore/retention and final acceptance remain Issues #9/#10.

## M1 adapter and live fetch behavior

The small `M1Provider` calls the actual existing browser_run with
parse_candidate_response, one direct sample, zero sample interval and the private
candidate callback added in Issue #4. HTTP retries, bounded 401 recovery, origin
checks, credentials handling and diagnostics remain M1's implementation. It does
not reconstruct authentication, export storage/tokens or scrape alternate data.

The callback captures only an immutable normalized candidate and its original
receipt time. Browser cleanup, navigation-log closure and profile lock release
occur before provider return; SQLite finalization happens afterward. Internal
transport/auth requests remain part of one observation, not extra snapshots.
Successful API validation alone is not successful ingestion: database failure
exits nonzero and preserves the accepted pointer.

The adapter verifies one callback, one direct sample and matching count. An empty
candidate is passed to the existing suspicious-response assessment, never accepted
automatically. Unknown/private browser-summary fields are dropped. Evidence contains
only counts and explicit credential-attempt/submission booleans when available.

Default M2 state is `.otter-runtime/.m1-state`, separate from `.otter-data` and
the original M1 checkout's state. Both overlap directions between database and
browser state are rejected. Symlinks, active-acceptance directories and unsafe
parents are rejected; M1's own private-state/profile lock checks still apply.
Do not point --state-dir at an ongoing M1 smoke/active/idle profile. No profile is
copied or automatically selected from another checkout. A new runtime parent is
created private; an existing parent must be owned and not group/other-writable.

Fetch is headless by default. Options:

```sh
./scripts/otter fetch --directory .otter-data \
  --state-dir .otter-runtime/.m1-state \
  --credentials-file "$HOME/.config/otter/credentials.env" \
  --decline-passkey --bootstrap-seconds 180 --request-seconds 30 \
  --state-seconds 60 --navigation-log
```

This is a **real login/API call when the owner runs it**, not an offline test.
Use the existing owner-only credential file outside the repository, with the
existing OTTER_USERNAME/OTTER_PASSWORD format. It is read only when explicitly
supplied. Neither passwords nor file contents are placed in CLI arguments/output
or SQLite. --decline-passkey requires that explicit file, matching M1 policy.
Without credentials the command is a strict existing-session attempt; it never
finds a .env implicitly. --headed allows a visible diagnostic/assisted run and is
not evidence of unattended behavior. Debug/TLS key logging variables are rejected
before starting browser work.

There is intentionally no --samples, --sample-interval or verify command in M2.
Use the existing M1 verify separately; it cannot create database observations.
--navigation-log is opt-in and uses M1's existing redacted private JSONL/report
mechanism under the selected state directory. Do not share/archive the profile.

## Offline imports and repeat processing

Example using **synthetic repository data**, with a stable ID generated once:

```sh
import_id="$(.venv/bin/python -c 'from uuid import uuid4; print(uuid4())')"
./scripts/otter import-json tests/fixtures/valid.json --directory .otter-data \
  --observation-id "$import_id"
./scripts/otter import-json tests/fixtures/valid.json --directory .otter-data \
  --observation-id "$import_id"
```

Use a disposable store for this demonstration; do not seed an operational calendar
with synthetic fixtures. A complete API response body is required; M1's redacted
stdout is not a response export and cannot be imported as timetable data.

Input must be a regular finite file, not a symlink/FIFO/device. The read is capped
by the existing 8 MiB limit and uses the existing HTTP/envelope/event parser.
No raw body or source filename is persisted. Personal fields are discarded by
normalization before storage. Errors never echo source data or exception messages.

The import service commits its start before file I/O and records invalid/failed
first imports. Valid imports freeze origin, start, original receipt and policy.
Repeating the same ID re-reads and validates the file against that original
context, then returns the same outcome without new rows or timestamps. Different
normalized content conflicts; private metadata differences that normalize away do
not create new content. Each intentional new import needs a new UUID.

An invalid/failed import retained no payload anchor: correct the file and use a
new ID. An abandoned uncaptured import is marked interrupted and cannot be
relabelled under its old ID. An anchored pending import can retry exact content
after a recoverable write failure; an interrupted terminal import stays interrupted.
Replaying unreadable/invalid input cannot overwrite a previously accepted result.
An existing live ID cannot be used for import. There is no --origin live override.

All imports, including reviewed ones, have eligible_for_sync=false. They can seed
data but cannot inflate later consecutive-live-observation counters. This flag
is future evidence only; counters and identities remain M3.

## Status, policy and deliberate review

```sh
./scripts/otter status --directory .otter-data
./scripts/otter observation OBSERVATION_UUID --directory .otter-data
./scripts/otter review OBSERVATION_UUID --directory .otter-data
```

Replace OBSERVATION_UUID with the actual ID from command output. Default review
shows status, counts, receipt instant, latest-observation flag, original baseline
attempt number and frozen policy settings. It prints no event payload or content
digest and performs no write. Check the actual portal/timetable scope before
confirming an anomaly. No automatic empty-response promotion is added.

Approval requires all of these flags:

```sh
./scripts/otter review OBSERVATION_UUID --directory .otter-data --approve \
  --expected-baseline BASELINE_ATTEMPT_NUMBER --reason confirmed_empty
```

Use the preview's positive baseline attempt number, or the literal `none` for
bootstrap. Reason must be confirmed_empty, confirmed_duplicates or confirmed_change
and match the held anomaly. Existing freshness/latest/baseline/content checks from
Issue #7 apply, including a two-hour receipt-age limit and rejection of clock-regressed
observations. Review keeps the same UUID/attempt and does not fetch or count again.
Supplying approval arguments without --approve is rejected rather than acted on.

Fetch/import accept --minimum-future-drop (integer >=1) and --future-drop-percent
(integer 0..100), default 5 and 30. Both decrease conditions must hold, evaluated
against unfinished baseline/candidate rows at one receipt instant. Large growth is
informational. Old rows/unknown locations remain valid. Review uses stored policy;
changing command defaults cannot silently reassess an old observation.

## Output, exit codes and recovery

Commands emit one JSON object to stdout (except static --help), without raw
tracebacks, paths, arguments, upstream messages, titles, locations or credentials.
Argument errors also avoid argparse's usual echo of invalid values. A mutation's
ok=true requires accepted durable ingestion; held/failed writes are nonzero.
Read-only status/observation/review-preview can return ok=true for a suspicious
record because the inspection succeeded; inspect its observation.status.

| Exit | Meaning |
|---|---|
| 0 | Command completed; fetch/import/review approval accepted |
| 2 | Authentication/access failure |
| 3 | Network failure |
| 4 | Invalid API/file response |
| 5 | Suspicious snapshot retained for review |
| 6 | Configuration/provider/local argument failure |
| 7 | Upstream service failure |
| 8 | Portal/authentication bootstrap failure |
| 9 | Database/storage failure, including a pending write |
| 10 | Observation-ID conflict or rejected review/ingestion operation |
| 130 | Interruption/interrupted import replay |

Unknown reason text is replaced by fixed fallback codes. Failed API calls do not
advance the accepted pointer. A pending database write reports its observation
UUID but never prints its private in-memory capture. There is no automatic new
login or GET in response to a database error. Library users can retain the
PendingObservationError context/capture for persistence-only retry; CLI callers
can inspect the observation and recover/fetch fresh after resolving storage:

```sh
./scripts/otter observation OBSERVATION_UUID --directory .otter-data
./scripts/otter recover --directory .otter-data
```

Recovery takes the store lock and cannot interrupt another conforming active
ingestion. A fresh fetch also performs recovery before creating its new UUID.
Do not initialize a missing/corrupt store as a substitute for recovering history.
No general backup/restore/retention command is added here; that is Issue #9.

## Offline tests and actual preparation results

35 new tests: 23 CLI, eight adapter and four import lifecycle tests. Integration
tests invoke the actual CLI -> adapter -> browser_run -> direct_fetch -> parser ->
SQLite path using the existing HTML-backed fake Playwright transport. They verify
credential-assisted/strict wet paths, response disposal/cleanup ordering, bounded
401 recovery, one observation, schema/API failure preservation, redacted output,
import replay/conflicts, policy configuration, review and recovery. No live service
is required for ordinary testing. The fake browser does not prove Microsoft JS,
Chromium startup or actual credentials work.

From the M2 checkout on macOS/PyCharm or Ubuntu:

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_m2_cli.py -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_m2_adapter.py -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_m2_import.py -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests scripts
sh -n scripts/setup scripts/m1 scripts/m1-acceptance scripts/otter .githooks/pre-commit
git diff --check
```

PyCharm: M2's .venv/bin/python, M2 working directory, Unittests target tests and
PYTHONPATH=src. Ubuntu uses the same commands; run uv sync --locked --no-build
only if setup is needed. No dependency/browser upgrade is included in this patch.
Expected full result: 351 discovered, 343 passes and eight browser skips.

Preparation on macOS arm64 / Python 3.12.14 / SQLite 3.53.1 / Playwright 1.62.0:
the offline suite passes with those counts. Explicit local Chromium fixtures still
have eight startup errors (TargetClosedError/SIGABRT), not passing assertions.
Compilation, shell syntax, staged privacy checks, exact applied contents/modes
and forward/reverse patch checks are verified in disposable checkouts. No live
authentication or Ubuntu execution is performed by the assistant. The owner
reported increments A–D passed on both hosts; live E evidence is still required.

Optional explicit local browser fixtures:

```sh
OTTER_BROWSER_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover \
  -s tests -p test_auth_browser.py -v
```

## Dedicated live integration — owner runs once on each host

After offline tests, from the M2 checkout on Mac and separately on Ubuntu, create
a **fresh private directory** outside the repo. Do not reuse M1 test profiles,
copy their state or modify the existing deployment. These commands perform real
university sign-ins/API GETs and create a separate test database:

```sh
.venv/bin/python --version
.venv/bin/python -c 'import sqlite3; print(sqlite3.sqlite_version)'
m2_test_root="$(.venv/bin/python -c 'import tempfile; from pathlib import Path; print(Path(tempfile.mkdtemp(prefix="otter-m2-live-")).resolve())')"
credentials_file="$HOME/.config/otter/credentials.env"

./scripts/otter init --directory "$m2_test_root/data"
./scripts/otter fetch --directory "$m2_test_root/data" \
  --state-dir "$m2_test_root/.m1-state" \
  --credentials-file "$credentials_file" --decline-passkey --bootstrap-seconds 180
./scripts/otter status --directory "$m2_test_root/data"

# New process, same dedicated profile, strict wet reuse without a credential file.
./scripts/otter fetch --directory "$m2_test_root/data" \
  --state-dir "$m2_test_root/.m1-state" --bootstrap-seconds 180
./scripts/otter status --directory "$m2_test_root/data"
```

The credential-file example must refer to your existing private file; never paste
its contents into chat/commands. Keep this shell open or retain the private root
path locally to locate its reports/profile later. No scheduling is installed.
Each wrapper call is a separate Python process. Successful fetches show accepted,
origin=live, eligible_for_sync=true and one direct sample. The first may show
credential attempts/submission; a healthy strict wet run should not require them.
Both statuses prove committed data can be read after process exit. After two
accepted fetches expect two attempts; identical contents share one content object,
while a genuine timetable change may create another. That difference is not a
test failure by itself. Capture only redacted JSON summaries and host/runtime
versions for review, never the browser profile or a raw API response.

If a step returns nonzero, stop and inspect its fixed category/reason and the
existing private M1 diagnostics. Do not repeat bad credentials, weaken validation,
approve an empty snapshot merely to make the test pass or touch M1's acceptance
profiles. No new 25/26-hour series is needed solely for this integration layer;
natural-expiry acceptance remains M1's independent gate.

## Apply, commit and reverse

```sh
cd "$HOME/Downloads/otter-m2"
git status --short --branch
git rev-parse HEAD
patch_file="$HOME/Downloads/OTTER_M2_Issue_8_CLI.patch"
git apply --stat "$patch_file"
less "$patch_file"
git apply --check "$patch_file"
git apply "$patch_file"
git diff --check
```

Require the exact base and clean milestone/m2-validation-storage branch.
Checking changes nothing; applying does not commit. Stop on conflicts rather
than partially applying with --reject. After offline tests and review:

```sh
git add -- .github/workflows/tests.yml .gitignore scripts/check_public_tree.py scripts/otter \
  src/otter/__main__.py src/otter/m1_adapter.py src/otter/ingestion.py \
  tests/test_auth_runner.py tests/test_m2_store_guard.py \
  tests/test_m2_cli.py tests/test_m2_adapter.py tests/test_m2_import.py docs/m2-cli.md
git diff --cached --check
git diff --cached
.venv/bin/python scripts/check_public_tree.py
git commit -m "feat(cli): connect M1 fetch to durable M2 ingestion" -m "Refs #8"
```

These are thirteen reviewed paths. The wrapper's executable bit is included.
The guard scans new indexed files. Refs links progress without closing the Issue;
no push, merge, PR, Issue edit or deployment is performed. Existing version-2
stores need no migration. If still on 1, explicitly migrate using the existing
maintenance command; initialize only a genuinely new store.

Before commit and without overlapping later edits:

```sh
git apply --reverse --check "$patch_file"
git apply --reverse "$patch_file"
```

If staged, unstage only the thirteen paths first (git restore --staged -- paths).
After commit use a reviewed git revert. Reversal removes CLI/adapter code but
does not remove schema-2 observations, database/backups or runtime profiles. The
new runtime ignore rule is reversed too: do not stage any runtime data that then
appears untracked. Preserve it privately outside the checkout with writers closed,
or keep a private local exclusion. Do not use reset-hard or git clean -fdx.

Wait for owner review/live test outcomes before Issue #9's backup/recovery work.
