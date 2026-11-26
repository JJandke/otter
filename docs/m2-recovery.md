# M2 increment F — verified backup and recovery (Issue #9)

Base: `043cdfb36749efe640fa91ebd9e9232f383be4b7` (committed Issue #8).
Prerequisites: increments A–E / Issues #4–#8, already in this base.
[Issue #9](https://github.com/JJandke/otter/issues/9) is the existing
“Add verified backup, restore guidance and private-data guards” Issue. Its body
was empty at inspection; the approved increment F scope is used. No new Issue,
commit, push, PR, production service or deployment is created by preparation.

## Commands and schema upgrade

Use the existing Python 3.12 environment and embedded sqlite3, with no SQL server,
ORM, Docker, dependency change or browser upgrade. From the M2 checkout:

```sh
.venv/bin/python --version
.venv/bin/python -c 'import sqlite3; print(sqlite3.sqlite_version)'
./scripts/otter --help
```

| Command | Effect |
|---|---|
| `check --directory DATA` | Full integrity, foreign keys, migration/schema shape, state and every available payload |
| `backup --directory DATA` | Verified new standalone file in DATA/backups; outputs its generated backup_name |
| `backup --directory DATA --backup-directory PATH` | Same operation in a dedicated private external directory |
| `verify-backup FILE.sqlite3` | Read-only check of a published standalone backup, no restore/migration/fetch |
| `restore FILE.sqlite3 --directory NEW_DATA` | Atomically publish a verified database into a new private store |
| `prune-quarantine --directory DATA` | Preview eligible payload expiry, no data changes/backups |
| `prune-quarantine --directory DATA --apply` | Verified pre-maintenance backup, then transactional payload expiry |

New schema **version 3** adds one nullable expiry timestamp to snapshot_content
and guards protecting accepted payloads. The five-table structure, observation
IDs, original receipts, stream UUID, offsets, accepted pointer and prior migration
checksums are preserved. No calendar identity/cancellation/ICS logic is added.

For an **existing M2 store**, pause only its writers/connections, then explicitly
check, back up and migrate it. Replace .otter-data with its actual directory if
you used the external temporary store from the live Issue #8 test:

```sh
./scripts/otter check --directory .otter-data
./scripts/otter backup --directory .otter-data
./scripts/otter migrate --directory .otter-data
./scripts/otter status --directory .otter-data
./scripts/otter check --directory .otter-data
```

Backup/check accept supported older schemas without upgrading them. migrate uses
the existing lock, verified pre-migration backup and single-transaction framework;
ordinary status/fetch/import require the current schema. A real upgrade adds an
independent pre-migration-v2-UUID.sqlite3 backup too; this helper now also publishes
only after successful integrity validation. Expect status schema_version=3
and unchanged attempts/accepted data. Missing/corrupt/unknown stores fail closed:
do not run init to replace lost history. init is only for a genuinely new store.

## What verification establishes

Backup holds the same nonblocking application lock as ingestion/review/migration.
It reads the actual SQLite store through Connection.backup(), not a filesystem
copy of a changing database. Each output is first a private hidden temporary file.
Copying is bounded by a 60-second callback budget (verification time is additional).
The destination passes full integrity_check, foreign_key_check, migration checksums,
expected schema-object comparison, accepted-pointer checks, every available content's
normalization/count/ordinal/digest checks and expiry invariants. Counts, stream and
recovered-through accepted/successful receipt metadata are checked against source.

After close and fsync, a hardlink publishes the complete file atomically without
overwriting any name; its temporary name is removed and the directory synchronized.
Repeated backups get UTC-dated UUID names. A caught pre-publication failure removes
only that invocation's temp; old backups/source remain intact. A post-publication
sync failure may leave a complete new file despite a nonzero command: inspect and
verify it rather than assuming it was removed. No automatic acceptance or recovery
of started observations occurs during backup/check/restore.

verify-backup opens a **published immutable backup**, read-only, and creates no
journal/lock/marker next to it. Do not modify it concurrently. Its source filename
must be a nonhidden .sqlite3 file other than otter.sqlite3, without journal/WAL/SHM
sidecars. Use check for an operational store. Files must be owner-only regular
files with one hardlink; their dedicated directory must be owner-only. Symlinked
ancestors, authentication/runtime directories, .git and the old production subtree
are rejected, as are unresolved .. path components. Use canonical absolute paths;
on macOS resolve temporary paths with
Path(tempfile.mkdtemp()).resolve(), since /tmp is often a symlink.

A database with an added table/trigger or different migration checksum is rejected,
even if SQLite integrity passes. This is corruption/compatibility detection, not
cryptographic authenticity or proof the upstream API was complete. Do not force
past backup_schema_mismatch: retain the files and record Python/SQLite versions.
Never open arbitrary untrusted files as application backups.

## Recovery runbook

1. Pause M2 invocations and close all connections, including IDE database tools.
   Do not stop/change production services or ongoing M1 acceptance as part of this
   development task. An application lock excludes conforming OTTER writers; it
   cannot close a forgotten reader or control arbitrary external SQL software.
2. Leave the original entire store intact, including .owner, .lock, database and
   journal. A missing/corrupt DB is not permission to delete/reinitialize it.
   SQLite handles its own hot rollback journal; never remove it as a repair shortcut.
3. Select a known backup and run verify-backup. Inspect schema, stream_uuid,
   attempt_count, last_accepted_attempt_no and last_accepted_received_at_utc_us.
   Data after the backup is not recovered. No command merges two histories.
4. Restore to a **new sibling/private directory**, not over the original:

   ```sh
   backup_file="/absolute/private/path/snapshot-TIMESTAMP-UUID.sqlite3"
   restored_dir="/absolute/private/path/otter-restored"
   ./scripts/otter verify-backup "$backup_file"
   ./scripts/otter restore "$backup_file" --directory "$restored_dir"
   ./scripts/otter check --directory "$restored_dir"
   ```

   Replace those example paths locally. Restore validates before creating the new
   store, uses the SQLite backup API and no-overwrite atomic publication, and retains
   the stream UUID/observation IDs/baseline. It refuses an existing database, unknown
   directory or leftover files. A clean marked directory left by a caught failed
   restore can be retried. No existing database, journal or backup is replaced.
5. If restore reports migration_required=true, explicitly migrate the restored
   directory before status/fetch, then check it again. Supported version-1/2
   pre-migration backups remain version 1/2 until this deliberate upgrade.
6. Compare redacted status/history counts and baseline against the backup report.
   Only then choose the restored path for subsequent M2 commands/configuration.
   Leave the damaged original preserved. Do not run original and restored as two
   active copies of one stream. No production path swap or scheduling is performed
   by the restore command. M5 will define service promotion separately.
7. Inspect started_count. After writers are closed, recover can mark abandoned
   starts interrupted. It does not invent a new successful observation or recover
   a response lost before persistence. Future fetching uses a new UUID as usual.

A SIGKILL/power loss may leave .backup-UUID.partial.sqlite3,
.migration-UUID.partial.sqlite3 or .restore-UUID.partial.sqlite3 for private inspection. They are never selected as
published backups; restore refuses a destination containing leftovers. Prefer a
fresh restore directory rather than deleting unknown files. In the narrow window
between hardlink publication and temporary-name removal, both names may refer to
one inode; private_file then deliberately rejects the two-link file. With all
writers closed, preserve/inspect the names, establish that they are this interrupted
operation's same inode, then remove only its temporary name. Never unlink an
unknown hardlink or database/journal automatically. Filesystems/hardware that do
not honor fsync, broken disks and live file copying are outside these guarantees.

## Explicit quarantine retention

Accepted observations and their completed/historical lectures are retained
indefinitely. There is no source-date cutoff. Failure/invalid/interrupted records
are compact and are not pruned. All observation rows and capture metadata remain;
separate identical observations are still distinct. Accepted payload protection
includes SQL guards against event-row update/delete and acceptance of an expired
payload. Retention never operates during normal ingestion automatically.

```sh
./scripts/otter prune-quarantine --directory .otter-data
# Only after reviewing the preview:
./scripts/otter prune-quarantine --directory .otter-data --apply
```

Default older-than-days=30; explicitly configurable integer 1..36500. A payload is
eligible only if it has suspicious references, **all** references are suspicious,
its creation and every referenced receipt/finish are strictly before the common
cutoff, and no started capture uses its digest/version. Any accepted or recent
reference protects shared content. Unreferenced objects are not assumed quarantine.
This uses observation times, not semester boundaries or lecture age/count changes.

Apply holds the store lock, makes a verified backup before any expiry, and deletes
eligible event rows plus marks the headers expired in one transaction. Backup
failure prevents expiry; SQL failure rolls it all back. Headers/counts/digests,
statuses/reasons/UUIDs and original timestamps remain. observation/review previews
show content_expired; loading expired event data fails with that fixed code.
Approval cannot reconstruct an expired payload. No cancellation counter advances.

An exact old-observation replay still returns its old outcome without new rows or
payload restoration. After expiry, comparison uses retained SHA-256/version/count
and original context; original canonical bytes are no longer available for the
additional byte comparison. A genuinely new observation can transactionally
rehydrate matching content under the same content header, then undergo normal
assessment. It does not relabel or count an old suspicious observation as accepted.
When rehydrated, that content is available to older references again; expiry is
payload availability, not an observation lifecycle state.

Freed SQLite pages are reusable; the file may not shrink. This is logical payload
retention, not secure erasure: backup copies/free pages may still contain old
lecture data. No automatic VACUUM or backup deletion is performed. Suggested
operator backup rotation is seven daily/four weekly verified copies plus needed
pre-migration backups; first verify newer copies and a restore before deliberate
manual removal. No schedule or deletion tool is installed here. An off-host copy
and its encryption/access policy belong to the deployment design.

## Growth and privacy

At hourly fetching there are approximately 8,760 small observations/year, regardless
of identical content. One changed snapshot stores a new normalized row set; there
is no per-event cross-snapshot version table yet. Preparation measured **synthetic**
100-event data with full ingestion: 168 identical accepted observations, one content,
100 event rows, 126,976 bytes; after 24 changed snapshots, 192 observations, 25 contents,
299,008 bytes. Those are local measurements, not a forecast or fixed size budget.
Measure actual growth and backup costs before optimizing; full checks scale with
all available contents. Retention only bounds explicitly maintained quarantine
payloads, not accepted history or manually retained backup copies.

Database and backups contain only normalized title/time/offset/location data plus
lifecycle metadata. Title/location may still reveal a person's timetable: files
remain private. No personid, email, instructor/group/datasource, raw API messages,
unknown fields, tokens/passwords/cookies or browser profile is included. Backup
copies the projected SQLite store, not the checkout/profile/data directory tree.
CLI shows fixed codes, counts, UUIDs and UTC integer receipt times, not lecture
payloads, paths, digests or raw exception text. UTC *_us values are microseconds
since the Unix epoch, not source-local wall time.

Git ignores .otter-backups plus existing database/runtime names; the publication
guard rejects force-staged database/backup/sidecar names and SQLite file headers
even under a renamed .bin filename. It remains a guard, not a comprehensive secret
scanner: archives, arbitrary renamed fragments and unrecognized formats remain
the operator's responsibility. Never stage actual data, force-add a backup, or
upload a browser profile/raw response for debugging. Synthetic canary tests check
normalization -> DB -> backup -> restore and error/report paths.

## Tests and disposable operator check

35 new tests: 20 backup/recovery, 14 retention and one publication test. Existing
schema tests now expect the current migration version; direct tampering tests
exercise suspicious content because accepted event updates are blocked earlier.
Tests use synthetic data, real file-backed SQLite and disposable child processes,
with no live authentication. They cover historical/all-state round trips, stream/
observation/baseline preservation, offsets, read-only verify, later source changes,
schema/checksum/corruption failures, version-2 upgrade and backup, no-overwrite,
unsafe paths, process locks, copy timeout/faults, SIGKILL backup/restore, expiry
rollback/protection/rehydration/replay and privacy canaries. Prior M1 tests remain.

Run on macOS/PyCharm and Ubuntu from M2 using its existing .venv:

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_m2_backups.py -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_m2_retention.py -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_m2_store_guard.py -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests scripts
sh -n scripts/setup scripts/m1 scripts/m1-acceptance scripts/otter .githooks/pre-commit
git diff --check
```

PyCharm: M2 working directory/interpreter, Unittests target tests, PYTHONPATH=src.
Ubuntu uses identical commands. If environment setup is actually needed use
uv sync --locked --no-build, without replacing M1's environment/browser cache.
Record the Python-linked SQLite version on both hosts; no server install is needed.

Preparation: macOS arm64, Python 3.12.14, SQLite 3.53.1, Playwright 1.62.0.
Expected full offline result is 386 discovered, **378 passed, eight skipped**.
The explicit Chromium fixture run still has eight startup errors
(TargetClosedError/SIGABRT), not passed assertions. No live authentication or
Ubuntu execution is performed by preparation. The owner reports A–E passed on
Mac/Ubuntu. Issue #10 will consolidate final acceptance evidence. No new 25/26-hour
or university login test is necessary solely for this backup/retention change.

Optional browser regression command on hosts where Chromium launches:

```sh
OTTER_BROWSER_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover \
  -s tests -p test_auth_browser.py -v
```

For a manual **offline** backup/restore check on each host:

```sh
umask 077
m2_test_root="$(.venv/bin/python -c 'import tempfile; from pathlib import Path; print(Path(tempfile.mkdtemp(prefix="otter-m2-recovery-")).resolve())')"
import_id="$(.venv/bin/python -c 'from uuid import uuid4; print(uuid4())')"
./scripts/otter init --directory "$m2_test_root/data"
./scripts/otter import-json tests/fixtures/valid.json --directory "$m2_test_root/data" \
  --observation-id "$import_id"
./scripts/otter backup --directory "$m2_test_root/data" > "$m2_test_root/backup-report.json"
backup_name="$(.venv/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["result"]["backup_name"])' "$m2_test_root/backup-report.json")"
./scripts/otter verify-backup "$m2_test_root/data/backups/$backup_name"
./scripts/otter restore "$m2_test_root/data/backups/$backup_name" --directory "$m2_test_root/restored"
./scripts/otter check --directory "$m2_test_root/restored"
./scripts/otter status --directory "$m2_test_root/restored"
./scripts/otter prune-quarantine --directory "$m2_test_root/restored"
```

Stop on nonzero; don't continue with an empty/missing report. Expected: schema 3,
one accepted import, same original observation UUID/baseline, one content, two event
rows and no eligible quarantine. No profile or credentials are needed. Keep this
root separate from real history; synthetic imports never become live sync evidence.

If migration_required/schema_migration_required appears, use explicit migrate on
the correct existing/restored directory. store_already_in_use means another process
holds its lock; do not delete .lock. backup_has_sidecars means the input is not a
standalone published backup; do not delete sidecars to force verification. A
backup_timeout or storage error exits 9 and preserves source/accepted history.

## Patch/Git workflow and reversal

```sh
cd "$HOME/Downloads/otter-m2"
git status --short --branch
git rev-parse HEAD
patch_file="$HOME/Downloads/OTTER_M2_Issue_9_Recovery.patch"
git apply --stat "$patch_file"
less "$patch_file"
git apply --check "$patch_file"
git apply "$patch_file"
git diff --check
```

Require clean milestone/m2-validation-storage and the exact base above; no new
worktree/branch is needed. --check changes nothing; apply installs without a commit.
Do not use --reject to partially install. Forward/reverse application and the
exact resulting tree/modes are verified in disposable checkouts before delivery.

After testing/review, stage these seventeen paths only:

```sh
git add -- .gitignore scripts/check_public_tree.py \
  src/otter/__main__.py src/otter/_store_files.py src/otter/ingestion.py \
  src/otter/migrations.py src/otter/storage.py src/otter/backups.py src/otter/retention.py \
  tests/test_m2_ingestion_recovery.py tests/test_m2_storage.py tests/test_m2_store_guard.py \
  tests/test_m2_backups.py tests/test_m2_retention.py \
  docs/m2-recovery.md docs/m2-cli.md docs/m2-storage.md
git diff --cached --check
git diff --cached
.venv/bin/python scripts/check_public_tree.py
git commit -m "feat(storage): add verified backups and recovery safeguards" -m "Refs #9"
```

Staging selects the reviewed source/tests/docs; the guard inspects indexed files.
The local commit records the increment and Refs #9 links progress without closing
the Issue. Nothing is pushed/merged/deployed automatically. Wait before Issue #10.

Before commit, with no overlapping edits and no data migration performed:

```sh
git apply --reverse --check "$patch_file"
git apply --reverse "$patch_file"
```

If staged, first git restore --staged -- followed by the same seventeen paths;
this keeps files for patch reversal. After commit use a reviewed git revert.
**Reversing source does not downgrade schema 3 or undo retention.** Old Issue #8
code cannot open version 3. Preserve current history, keep current compatible code
until a deliberate recovery plan is ready, or restore a verified version-2 backup
to a new directory before reverting source; that old backup lacks newer observations.
Never edit migration history, replace newer history blindly or use init/reset-hard/
git clean -fdx as recovery. Runtime/backup files remain private and uncommitted.
Reversal also removes the new backup ignore rules: do not stage newly visible data.
