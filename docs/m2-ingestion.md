# M2 increment D — atomic, retry-safe ingestion (Issue #7)

Base: `6cdd8d1b14e63f96d0dc407d818bce2fd3428aaf`, the owner's committed
Issue #6. Prerequisites: increments A–C, already included in this revision.
Addresses [Issue #7](https://github.com/JJandke/otter/issues/7) in the existing M2
milestone. No original worktree/profile/database, production service, GitHub
Issue/PR or remote branch is changed by preparing this patch.

## Scope

`otter.ingestion` implements a provider-independent observation lifecycle,
captured-receipt replay, recovery and deliberate review. Providers are injected
callables; this increment does not authenticate, call the university, add live
fetch/import/review CLI commands or schedule anything. The M1 CLI and transport
remain unchanged. Issue #8 will supply the actual M1 adapter and M2 commands.
No event UIDs, event matching, cancellation counters or ICS output are added.

The approved Issue #5 policy is reused: large growth is informational, naturally
completed lectures are excluded only from the common-time anomaly metric, empty
results remain suspicious, and old observations/unknown locations remain valid.
No previous accepted content is replaced by a failed, invalid, interrupted or
unreviewed suspicious response.

## Required database migration

This increment appends **schema version 2**; migration 1 is unchanged. Its
original checksum remains
`2d7140d2c9d89720db5c70623bb42fb7f7b558e9ddc3e2d0e79fddbb3d300efd`.

For an existing Issue #6 database, after applying and testing the patch:

```sh
cd "$HOME/Downloads/otter-m2"
.venv/bin/python --version
.venv/bin/python -c 'import sqlite3; print(sqlite3.sqlite_version)'
PYTHONPATH=src .venv/bin/python -m otter.storage migrate --directory .otter-data
PYTHONPATH=src .venv/bin/python -m otter.storage status --directory .otter-data
PYTHONPATH=src .venv/bin/python -m otter.storage schema --directory .otter-data
```

Migration takes the existing store lock and creates a verified private version-1
backup before its atomic schema upgrade. Expected status: schema version 2 and
the same preexisting observation/content counts and accepted pointer. Normal
opening refuses version 1 until this explicit upgrade; it never silently migrates.
The new tests upgrade a real version-1 database with synthetic accepted data and
verify data/checksum preservation and the independent backup's version.

If no store was ever created, use `otter.storage init --directory .otter-data`
once instead. It creates version 2 directly. Do not initialize again to replace
an existing database, and do not run any command against an active M1 profile.
Use the same commands on Ubuntu from its isolated M2 checkout; no SQL service,
Docker, ORM or new package is needed.

Version 2 adds only observation metadata: immutable capture fingerprint/version/
count/receipt-time fields; JSON lists restricted by application validation to
known assessment/information codes; and a fixed review reason. These are not raw
responses or arbitrary diagnostic blobs. The digest stays private and is omitted
from normal status/ingestion summaries. Version-1 observations are retained;
missing receipt anchors are not fabricated retrospectively as live sync evidence.

## Lifecycle and transaction boundaries

`IngestionService(store, clock=..., policy=...).fetch(provider, origin='live')`:

1. Generates a new observation UUID and acquires the nonblocking store lock.
2. Marks abandoned `started` attempts interrupted while holding that lock.
3. Commits one `started` attempt with the original policy settings.
4. Calls the provider exactly once, with **no SQL transaction open**. The existing
   M1 adapter may later perform its own bounded HTTP/auth recovery inside this
   one provider invocation; those requests are not separate observations.
5. Validates and anchors the received normalized content fingerprint, its original
   receipt time and format/count in a short metadata transaction.
6. In a separate short transaction, reads the current accepted baseline, assesses
   the candidate, inserts/reuses content, finalizes the attempt and, only on
   acceptance, moves the accepted pointer. All final data/state/pointer writes
   commit together or roll back together.

The store lock covers the complete sequence but is not a network-length SQLite
write lock. Other OTTER ingestion/recovery/review operations cannot overlap it.
Raw unmanaged SQLite writers are still subject to the existing finite busy
timeout; normal operation should not modify the store outside these APIs.

The provider returns `CapturedSnapshot(candidate, received_at)`, where candidate
comes from the existing `parse_candidate_response` boundary. The response body's
personal metadata has already been discarded. Receipt time must be the actual,
original aware instant; do not substitute the time of a retry. A provider returning
an arbitrary dictionary/count-only M1 summary fails its contract.

Example API wiring, assuming an already initialized/migrated private store and
an existing trusted provider callable (not a runnable live-fetch command yet):

```python
from pathlib import Path
from otter.ingestion import IngestionService
from otter.storage import Store

with Store(Path(".otter-data")) as store:
    service = IngestionService(store)
    result = service.fetch(provider, origin="live")
    safe_summary = result.safe_summary()
```

Use `origin='import'` for saved-file input. Accepted imports may seed data but
`eligible_for_sync` is false, and they do not become a last successful **live**
fetch. Do not relabel file replay as a new live retrieval. The origin is trusted
application context; Issue #8's CLI must enforce this separation.

## Outcomes and privacy

| State | Meaning |
|---|---|
| `started` | Committed start; final processing has not committed |
| `accepted` | Content, final metadata and pointer committed atomically |
| `suspicious` | Complete normalized candidate retained for review; pointer unchanged |
| `invalid` | Actual parser rejected the response; no event content stored |
| `failed` | Authentication/network/service/provider failure; no event content stored |
| `interrupted` | Operator interruption or recovered abandoned start |

Typed M1 errors are classified without copying their diagnostics or arbitrary
messages. Only known reasons survive; other reasons become fixed fallback codes.
KeyboardInterrupt is recorded and re-raised. Other nonstandard provider exits are
also recorded as interrupted and normalized to a message-free KeyboardInterrupt,
so an upstream exit string cannot be printed accidentally. Invalid/failed
responses cannot become a baseline. A legacy count-only provider raising an empty
response exception has supplied no candidate to assess: it is recorded as a
failed retrieval, not a fabricated suspicious snapshot. The proper future adapter
must use the candidate-producing parser so valid empty data can be quarantined.

`ObservationResult` is immutable and has a `safe_summary()` with IDs, status,
fixed reason/category/codes and sync-eligibility/review flags. There are no titles,
locations, account identifiers, emails, tokens, raw payloads or content digests in
that summary. Captures retain private normalized rows in memory but omit them from
repr. Do not serialize captures, raw internal database rows or exception dicts
into logs. Deliberate data inspection remains private.

## Exact replay and database failures

`ObservationContext(uuid, origin, started_at)` identifies a retrieval/import.
`ingest_captured(context, capture)` records or retries that **same** captured
observation; it never invokes a provider. Equality uses original UTC instants,
origin and normalized content/version/count, independent of source row ordering.

- Same UUID/context/capture returns the original recorded result without creating
  rows, changing timestamps or moving a pointer back, even after later observations.
- Different data, origin, start or receipt time under that UUID raises a fixed
  conflict. An existing captured receipt's context/policy anchor cannot be changed.
- A retry of a pending receipt uses its originally recorded policy, not a newly
  configured threshold. Completed outcomes are never reassessed automatically.
- Three distinct successful hourly UUIDs can reference one deduplicated content
  object and remain three observations. Hashes must never be used as a substitute
  for counting observations in M3.

A final write failure rolls back new event/content rows, final status and pointer.
The prior committed start/anchor remain. An optional best-effort fixed storage
error is recorded on that still-pending attempt; inability to write that metadata
does not erase its earlier start. There is no automatic network or credential retry.

If `fetch()` cannot finish persistence it raises `PendingObservationError` with
only `observation_write_pending` in its message. Its private `context`/`capture`
attributes allow persistence-only retry while those objects are available:

```python
from otter.ingestion import PendingObservationError

try:
    result = service.fetch(provider)
except PendingObservationError as pending:
    # After addressing the storage problem; never call provider again here.
    result = service.ingest_captured(pending.context, pending.capture)
```

This illustrates a deliberate retry, not an unbounded retry loop. The exception
attributes contain a private normalized timetable: do not log its dict. Lower-level
`ingest_captured` failures leave the caller's original context/capture available too.

If a process dies, a later reader can reopen the previous valid database; SQLite
handles its journal. `recover_interrupted()` obtains the store lock before marking
all abandoned starts interrupted. A new fetch/captured-observation UUID invokes
that recovery before starting. It cannot interrupt another conforming active
writer because lock contention fails first. Recovery is repeatable and never
advances sync eligibility or the accepted pointer.

After recovery, matching replay of an anchored interrupted receipt returns its
interrupted result; it cannot promote it to accepted. A new retrieval needs a new
UUID. If the body was lost in a crash before its final content transaction, its
hash cannot reconstruct it; recovery does not invent missing data. Resume only an
available original captured receipt before it is superseded/recovered. Legacy
version-1 terminal records with no anchor cannot be verified against a new capture.

## Deliberate review

`approve(uuid, expected_baseline_attempt_no=..., reason=...)` is an in-process API;
Issue #8 will expose the operator command. It targets one recorded suspicious
observation, not a global accept-empty option.

Requirements under one lock/write transaction:

- It is the latest observation, including newer failed attempts in that check.
- Its original receipt is no more than two hours old and is not in the future.
- The caller's expected baseline matches both the original recorded baseline and
  the current accepted pointer (`None` for bootstrap).
- Stored content passes normal count/version/digest/field validation. Assessment
  under the original receipt time/policy/baseline reproduces the held reasons.
- The fixed reason matches an applicable anomaly: `confirmed_empty`,
  `confirmed_duplicates` or `confirmed_change`. No arbitrary review notes are stored.

Clock-regressed observations require a new correctly timed observation; review
does not retroactively reorder them. Invalid/failed/interrupted/automatically
accepted observations cannot be promoted using review. Duplicate multiplicity is
preserved if deliberately confirmed.

Review transitions the same attempt to accepted, records operator method/reason
and acceptance time, and updates the pointer atomically. Original assessment
reasons remain for audit. It creates no extra fetch or observation. Repeating the
same review returns the recorded result without moving a newer pointer backwards;
conflicting review arguments fail. Reviewed imports remain sync-ineligible.

`eligible_for_sync` currently means accepted + live origin + a recorded capture.
It is evidence for later M3 consumption, not an implemented cancellation counter.
M3 must consume each accepted eligible observation once in order, preserve completed
lectures, and perform conservative matching. Counts alone still cannot prove that
the API returned a complete timetable. No lecture history is pruned here.

## Tests and expected results

45 new tests exercise atomic finalization, duplicate content/distinct observations,
idempotent/conflicting replay, frozen retry policy, provider/error privacy, rejected
updates, application-lock/SQL transaction boundaries, competing writers, real process
termination, fresh-process retry, interrupted-start recovery, review freshness/
baseline/content checks and a real version-1-to-2 upgrade with data and backup.
Existing migration tests now simulate the next schema after the shipped version;
they still check checksum/newer-version gates and transactional DDL rollback.

macOS/PyCharm terminal, after patch application:

```sh
cd "$HOME/Downloads/otter-m2"
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_m2_ingestion*.py' -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests scripts
sh -n scripts/setup scripts/m1 scripts/m1-acceptance .githooks/pre-commit
git diff --check
```

Expected: 45 focused passes; 316 discovered in full offline discovery, 308 passes
and eight opt-in browser skips. Tests create/migrate disposable databases; they do
not require migration of your own store or any Microsoft authentication. In PyCharm
keep M2's interpreter/working directory, Unittests target `tests`, `PYTHONPATH=src`.

Ubuntu uses the same commands in its isolated M2 checkout. Run
`uv sync --locked --no-build` first only if environment setup is needed. Record
the Python-linked SQLite version on both hosts. No SQL server install is needed.
Optional local browser regressions, with the existing environment:

```sh
OTTER_BROWSER_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover \
  -s tests -p test_auth_browser.py -v
```

Preparation uses macOS arm64 / Python 3.12.14 / Playwright 1.62.0 / SQLite 3.53.1.
The owner reported increments A–C passed on Mac and Ubuntu. This increment's
preparation performs no live sign-in or Ubuntu execution; Work's explicit browser
run still has eight startup errors (TargetClosedError/SIGABRT), not passing fixture
assertions. Pure ingestion changes do not require a new 25/26-hour auth series.
Final test results, exact patch content/mode comparisons and forward/reverse
application are verified in a separate disposable checkout before delivery.

## Apply, review and commit

```sh
cd "$HOME/Downloads/otter-m2"
git status --short --branch
git rev-parse HEAD
patch_file="$HOME/Downloads/OTTER_M2_Issue_7_Ingestion.patch"
git apply --stat "$patch_file"
less "$patch_file"
git apply --check "$patch_file"
git apply "$patch_file"
git diff --check
```

Require a clean `milestone/m2-validation-storage` at the exact base above.
`--check` changes no files; applying creates no commit. Stop on conflicts rather
than using --reject. After successful tests and review, stage only these ten paths:

```sh
git add -- src/otter/ingestion.py src/otter/migrations.py src/otter/storage.py \
  tests/test_m2_ingestion.py tests/test_m2_ingestion_recovery.py tests/test_m2_ingestion_review.py \
  tests/test_m2_migrations.py tests/test_m2_storage.py docs/m2-ingestion.md docs/m2-storage.md
git diff --cached --check
git diff --cached
.venv/bin/python scripts/check_public_tree.py
git commit -m "feat(ingestion): persist observations atomically with safe retries" -m "Refs #7"
```

Staging selects the reviewed files. The guard scans new indexed files too.
The commit records local progress; it does not push, merge, deploy or close an
Issue. No new Issue/milestone/PR is created by this work.

Before commit and without overlapping edits:

```sh
git apply --reverse --check "$patch_file"
git apply --reverse "$patch_file"
```

If staged, unstage just those ten paths first (`git restore --staged -- <paths>`).
After commit, use a reviewed `git revert <commit-sha>`. Source reversal does not
downgrade databases or delete data/backups. If your store has been upgraded to 2,
the old Issue #6 code will reject it as newer than supported. Keep the new code
while pausing operations, or restore a separately verified compatible version-1
backup with writers/connections stopped and current DB/journal preserved. Never
edit version/checksum rows, rerun init, delete a journal or reset-hard/git-clean
as a substitute for database recovery. The supported restore command remains #9.

Wait for owner tests/review before Issue #8's actual M1-to-M2 CLI integration.
