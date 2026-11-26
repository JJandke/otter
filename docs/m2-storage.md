# M2 increment C — SQLite repository and migrations (Issue #6)

**Increment F update:** the current schema is version 3. Existing version-1/2
stores must explicitly migrate before ordinary opening. See
[m2-recovery.md](m2-recovery.md) for current checks, verified backup/restore and
quarantine maintenance. Version-1 details and test counts below describe the
original increment C. Do not initialize again to replace an existing store.

Base: `b2de4f42189e8874819c36384f141b0c26b9d55f` (committed Issue #5).
Prerequisites: committed increments A and B, already included in that base.
This patch addresses [Issue #6](https://github.com/JJandke/otter/issues/6), in the
existing M2 milestone. It does not create/edit Issues, commits or PRs, or change
the original M1/M2 working directories, authentication state or production.

## What is implemented

- Python's embedded `sqlite3`, with no database server, Docker, ORM or dependency
  changes. Preparation used Python 3.12.14 linked to SQLite 3.53.1 on macOS arm64.
- Five-table version-1 schema, explicit private initialization, schema inspection,
  status queries and explicit forward migration.
- A small repository for deduplicated normalized contents and starting attempt
  records; short explicit SQL transactions and a reentrant application lock.
- Content count/version/digest verification, SQL constraints, accepted-pointer
  checks, private filesystem checks and fixed errors without raw exception text.
- Verified pre-migration backups and transactional DDL/version changes.
- Force-staged database/sidecar/private-store publication checks, moved forward
  from #9 because this increment introduces runtime database artifacts.

Issue #7 still owns observation finalization, assessment-to-storage orchestration,
idempotent retries, interrupted-attempt classification and candidate review.
Issue #8 owns live fetch/ingestion commands. General backup/restore/retention
commands remain #9. There is no live ingestion, permanent event identity,
cancellation handling, ICS generation, deployment or scheduling here.

## Create and inspect a database

Run from the existing M2 checkout after applying and testing the patch:

```sh
cd "$HOME/Downloads/otter-m2"
.venv/bin/python --version
.venv/bin/python -c 'import sqlite3; print(sqlite3.sqlite_version)'
PYTHONPATH=src .venv/bin/python -m otter.storage init --directory .otter-data
PYTHONPATH=src .venv/bin/python -m otter.storage status --directory .otter-data
PYTHONPATH=src .venv/bin/python -m otter.storage schema --directory .otter-data
PYTHONPATH=src .venv/bin/python -m otter.storage migrate --directory .otter-data
```

Only `init` creates a database. It publishes a completely initialized database,
refuses to replace an existing one, and creates a new random local stream UUID.
An empty newly initialized database has schema version 1, zero attempts/contents/
event rows, and null last-accepted/last-successful values. The current `migrate`
is a successful no-op because no version after 1 is shipped yet.

`status` reports counts and allowlisted observation metadata; `schema` emits JSON
containing static table/index/trigger SQL, without stored lecture rows. Commands
emit `ok:true` and exit 0, or a fixed `ok:false` reason and exit 1. They perform
no authentication and do not fetch timetable contents. Nothing should be manually
inserted into an operational database to simulate a successful ingestion.

The same commands work on Ubuntu, from its isolated M2 checkout. Use that
environment's Python 3.12 interpreter and record its linked SQLite version; no
`apt install` of an SQL server is needed. No production service changes occur.

The resulting dedicated directory contains:

| File | Purpose |
|---|---|
| `.owner` | Fixed private-store marker |
| `.lock` | Application advisory lock |
| `otter.sqlite3` | Database |
| `otter.sqlite3-journal` | Possible transient/recovery rollback journal |
| `backups/pre-migration-vN-UUID.sqlite3` | Verified backup before a real upgrade |

Directories must be owned by the current account and private (0700); files must
be owner-only regular files (normally 0600), without symlinks or hardlinks.
Use a canonical path: symlinked ancestors are rejected too. On macOS `/tmp` is
usually a symlink; for a disposable experiment use a resolved temporary directory
from `Path(tempfile.mkdtemp()).resolve()` rather than a literal `/tmp/...` path.
Authentication state/profile/test directories, `.git` and the old production
application subtree are rejected. Existing shared directories are not chmodded
or repurposed; choose a new dedicated private directory.

Default runtime files and backups are ignored by Git. The publication guard also
rejects force-staged `.otter-data`, `.sqlite*`, database backups/sidecars and
`.db-journal`/`.db-wal`/`.db-shm` names. Arbitrary backup exports outside that
naming/layout remain the operator's responsibility; never commit actual data.

## Schema and lifecycle boundaries

| Table | Responsibility |
|---|---|
| `schema_migration` | Sequential version, exact SQL checksum and UTC application time |
| `snapshot_content` | Unique `(normalization_version, format_version, digest)`, row count and creation time |
| `snapshot_event` | Canonically ordered normalized rows, keyed by content and ordinal |
| `fetch_attempt` | Unique observation UUID, origin, lifecycle state, timestamps, counts, content/baseline links and fixed diagnostic fields |
| `store_state` | Singleton stream identity and last-accepted attempt pointer |

The six attempt states are `started`, `accepted`, `suspicious`, `invalid`, `failed`
and `interrupted`. Origins are `live` or `import`. CHECK/foreign-key/unique
constraints enforce essential state/timestamp/content combinations. Accepted and
suspicious rows require a complete metadata shape and matching reported count.
The accepted pointer must identify the latest accepted attempt. Accepted attempt
records cannot be updated/deleted, and an existing pointer cannot be cleared.

Event data consists only of title, integer UTC start/end microseconds, both source
offsets and nullable building/room. There are no account IDs, email, instructor,
group, datasource, API messages, tokens, credentials, cookies, raw payloads or
browser paths in the schema. Fixed metadata columns provide no arbitrary raw
diagnostic JSON field. Later ingestion must still allowlist any error reason it
supplies; the low-level SQL interface is for trusted application code only.

Three successful observations can share one content object/event set while
retaining three distinct attempt records. C tests seed final observation metadata
directly with synthetic SQL fixtures to verify schema/query behavior; that helper
is not an implemented application ingestion service.

`last_successful_fetch()` selects the latest **live** attempt with completed
structural validation, including a suspicious candidate. HTTP 200 alone does not
qualify. `last_accepted()` follows the accepted pointer, which may include an
explicit import. `accepted_baseline()` reconstructs that observation for assessment.
The later ingestion layer must exclude imports from cancellation evidence.

Stored ordinals represent sorted snapshot rows, not lecture identities. Loading
content checks supported versions, count, contiguous ordinals, normalized field
validity and recomputed digest. Reuse of an existing digest also compares the
actual canonical bytes, preventing a conflicting payload from overwriting data.
Manual SQL tampering may bypass application-level guarantees, but changed content
is rejected on load. Do not edit the database manually during normal operation.

## Repository usage

```python
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from otter.storage import Store

# candidate must be a normalized SnapshotCandidate from the existing parser.
with Store(Path(".otter-data")) as store:
    with store.transaction():
        attempt_no = store.begin_attempt(
            str(uuid4()), origin="live", started_at=datetime.now(timezone.utc)
        )
        content_id = store.put_content(candidate, created_at=datetime.now(timezone.utc))
    restored_candidate = store.load_content(content_id)
```

This demonstrates repository primitives only: the example leaves a `started`
attempt, does not finalize success, and is not a production ingestion command.
Issue #7 will separate the committed attempt start from later network work and
finalize state/content/pointer together. Duplicate UUIDs currently fail a unique
constraint; automatic same-observation idempotent replay belongs to #7.

Writes require `transaction()`, which yields the trusted low-level connection for
future orchestration. Do not swallow write exceptions inside its body. An exception
escaping the context, including KeyboardInterrupt, rolls back the transaction.
Nested transactions are rejected; `exclusive()` may wrap several sequential
transactions to let #7 hold a lock across fetch-to-commit without holding SQL locks
during network work. Store connections follow sqlite3's same-thread restriction.

The database is opened with URI `mode=rw`, explicit `autocommit=True`, foreign
keys on, rollback journal mode DELETE, synchronous FULL and a five-second busy
timeout. Writes use SQL BEGIN IMMEDIATE/COMMIT/ROLLBACK rather than Python's
no-op commit/rollback methods in autocommit mode. Other OTTER writers fail a
nonblocking application lock; unmanaged external SQL writers get a bounded SQLite
busy failure. A lock failure is not guaranteed to be recordable in the locked DB.

References: [Python sqlite3](https://docs.python.org/3.12/library/sqlite3.html),
[SQLite PRAGMAs](https://sqlite.org/pragma.html),
[SQLite backup API](https://sqlite.org/backup.html).

## Migration and recovery

Normal opening never upgrades, recreates a missing file, repairs corruption or
accepts a newer schema. Migration versions must be contiguous, and previously
applied checksums must match shipped SQL exactly. Never edit migration 1 after
release; add a new migration for later changes.

An explicit real upgrade locks the application, validates the old database,
creates an owner-only backup using Connection.backup(), checks full integrity and
foreign keys, and synchronizes the backup before applying changes. Backup has a
60-second budget; failure prevents schema changes. Pending SQL statements and
version records run in a single transaction, without executescript or silent
implicit commits. A failed migration restores the previous schema/version through
rollback and leaves the verified backup available. Tests simulate version 2;
only version 1 is actually shipped in this patch.

A missing/corrupt/unreadable database fails closed and is preserved. Do not rerun
`init` to replace lost history. SQLite recovers its own hot rollback journal;
never delete a journal as a repair shortcut. quick_check/foreign_key_check run on
open, with full integrity checks around migration/backup. These checks do not
establish API completeness or detect every hardware failure.

Initialization uses a private temporary database and atomically links the finished
file into place without overwriting an existing target. Failed pre-publication
initialization can be retried in the marked directory. A forcibly terminated init
may leave a private `.init-UUID.sqlite3` file for inspection. In the narrow crash
window after linking but before removing that temporary name, the final DB has a
second hardlink and opening deliberately fails. Preserve both names, verify they
are the same inode and that this was an initialization interruption, then remove
only the temporary initialization name before retrying status. Never remove an
unknown hardlink or an existing database automatically.

Restoration after a committed upgrade is not reverse-patching Python code: stop
writers, preserve the current database/journal and use a verified compatible
backup. A supported restore command and complete runbook remain #9. No history
or quarantine pruning is implemented here. Source data and backups stay private.

## Tests and preparation results

There are 38 new offline tests: 27 repository tests, nine migration tests and two
publication tests. They use disposable file-backed databases, synthetic API rows
and harmless child processes. Covered: private init/open, schema/constraints,
three-observation deduplication, exact offset round trips, content conflict/tamper
detection, fresh-process durability, killed-writer rollback, pointer preservation,
UUID uniqueness, bounded busy failure, process locks, schema version/checksum
gates, failed DDL/version rollback, verified backup and forced-staging privacy.

The owner reported A/B tests passed on macOS and Ubuntu. This increment's
preparation uses macOS arm64 / Python 3.12.14 / Playwright 1.62.0 / SQLite 3.53.1.
No live authentication or Ubuntu execution is performed by patch preparation.
The full suite should discover 271 tests, with eight opt-in browser skips.

From the M2 checkout, in Terminal or PyCharm's terminal:

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_m2_storage.py -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_m2_migrations.py -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_m2_store_guard.py -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests scripts
sh -n scripts/setup scripts/m1 scripts/m1-acceptance .githooks/pre-commit
git diff --check
```

PyCharm: M2's `.venv/bin/python`, M2 working directory, Unittests target `tests`,
`PYTHONPATH=src`. On Ubuntu use the same commands from its isolated M2 checkout;
if needed, first run `uv sync --locked --no-build`. Run the version commands above
on both hosts. No credentials or browser are needed for new storage tests.

Optional local Chromium regressions:

```sh
OTTER_BROWSER_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover \
  -s tests -p test_auth_browser.py -v
```

Expected new results: 27 + 9 + 2 passes. Full offline discovery should report
271 discovered, 263 passed, eight skipped. Work's explicit Chromium run still
fails to launch: eight attempted, eight startup errors (`TargetClosedError`,
SIGABRT), not successful fixture assertions. No new 25/26-hour auth run is needed
for these storage-only changes. Stop on failed assertions, unexpected modes or
schema errors rather than loosening checks. Final forward/reverse patch checks,
exact applied-content comparison and the offline suite run on applied files are
performed in a second disposable checkout before delivery.

## Patch workflow

```sh
cd "$HOME/Downloads/otter-m2"
git status --short --branch
git rev-parse HEAD
patch_file="$HOME/Downloads/OTTER_M2_Issue_6_SQLite.patch"
git apply --stat "$patch_file"
less "$patch_file"
git apply --check "$patch_file"
git apply "$patch_file"
git diff --check
```

Require a clean `milestone/m2-validation-storage` branch and the documented base.
Checking does not change files; applying creates no commit. Stop on conflicts;
do not partially install with --reject. After testing, stage the nine paths:

```sh
git add -- .gitignore scripts/check_public_tree.py \
  src/otter/_store_files.py src/otter/migrations.py src/otter/storage.py \
  tests/test_m2_storage.py tests/test_m2_migrations.py tests/test_m2_store_guard.py \
  docs/m2-storage.md
git diff --cached --check
git diff --cached
.venv/bin/python scripts/check_public_tree.py
git commit -m "feat(storage): add versioned SQLite snapshot repository" -m "Refs #6"
```

Staging selects reviewed files; the guard checks new indexed files too. No push,
merge, PR, Issue closure or deployment is included. Before commit and without
later overlapping edits, reverse with:

```sh
git apply --reverse --check "$patch_file"
git apply --reverse "$patch_file"
```

If staged, unstage only the nine paths first (`git restore --staged -- <paths>`).
After commit use a reviewed `git revert <commit-sha>`. Neither reversal method
deletes private databases/backups or reverses schema changes. Keep those files
intact; do not use git clean -fdx or reset-hard. Wait for review/tests before
Issue #7's ingestion lifecycle work.

Reversing this patch also removes its new `.otter-data/` ignore rule. If you
created a store there, Git may then show its marker/lock as untracked. Do not stage
the runtime directory. With all connections/writers closed, preserve it outside
the checkout or keep a private local exclusion until the patch is reapplied.
