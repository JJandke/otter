# M2 increment A — normalized candidates (Issue #4)

Base: `9f656c04500549c019a6d611d7b05ba7f6f2a47e`. No prerequisite
patches. This increment implements only Issue #4. It has no database writes,
baseline assessment, calendar identities, cancellation tracking or ICS output.

## Existing GitHub references

The existing milestone is **M2 – Validation & Storage**, milestone 2. This is
the owner's existing milestone; do not create a second "Persistence" milestone.
The inspected issue titles map to the approved implementation order:

| Order | Issue | Scope |
|---|---|---|
| A | [#4](https://github.com/JJandke/otter/issues/4) | Normalized candidates and M1 data handoff |
| B | [#5](https://github.com/JJandke/otter/issues/5) | Snapshot trust and anomaly assessment |
| C | [#6](https://github.com/JJandke/otter/issues/6) | SQLite schema/repository/migrations |
| D | [#7](https://github.com/JJandke/otter/issues/7) | Atomic, retry-safe ingestion |
| E | [#8](https://github.com/JJandke/otter/issues/8) | M2 CLI and M1 integration |
| F | [#9](https://github.com/JJandke/otter/issues/9) | Backup, recovery and privacy guards |
| G | [#10](https://github.com/JJandke/otter/issues/10) | Milestone acceptance and documentation |

Issue #4 had no body at inspection; its title and the approved Phase B request
define this increment's scope. No Issue was edited/closed and no PR was created.

## Boundary and normalization

`provadis_m1.validation.validate_response` retains its existing count-only return,
required building/room keys, error categories and suspicious-empty behavior.
`parse_candidate_response(status, content_type, body)` is the opt-in M2 path.
Both use the same HTTP, JSON, envelope, count and event validation routine.

The M2 parser returns a frozen `SnapshotCandidate` containing a tuple of frozen
`NormalizedEvent` rows. Every row must validate before a candidate is returned.
Only title, start/end instants, source offsets, building and room survive.
Instructor, group, account/person ID, email, datasource, messages and arbitrary
extra fields are never included in the projection. Existing optional metadata
type checks remain in the shared validator, even for fields later discarded.

UTC instants are integer microseconds from the Unix epoch, computed without
floating-point timestamps. Original offsets are retained independently as whole
seconds. There is no inferred IANA timezone or dependency on host local time.
Spring/autumn changes compare actual instants. Source offset fractions finer
than one second are rejected by M2 rather than truncated; the legacy M1 wrapper
keeps its prior parsing behavior. Such offsets have not been observed in the API.

Title/location strings use Unicode NFC and outer whitespace trimming. Case and
internal whitespace remain unchanged. Ill-formed Unicode surrogates cannot enter
the normalized projection. Missing, null, empty and whitespace-only locations
become `None`; populated locations remain distinct. Blank entries may represent
Teams lectures, but this is not a documented guarantee: no Online/Teams label or
location inference from datasource is implemented.

Old events are preserved without a cutoff or special anomaly rule. In particular,
the erroneous 2023 entry supplied by Provadis does not justify rejecting its
snapshot. There is no calendar filtering. Source order and exact duplicate
multiplicity are retained. Repeated titles are not merged or assigned identities.
Event details are omitted from model reprs, but deliberate serialization still
contains timetable details: keep candidates private and never log them wholesale.

## Private M1 handoff

`direct_fetch(..., validator=None)` defaults to the existing M1 validator.
`browser_run(..., snapshot_validator=None, on_snapshot=None)` exposes an opt-in
in-process handoff after each successfully parsed direct sample. Example for a
future adapter, after acquiring the existing profile lock:

```python
received = []
summary = browser_run(
    profile,
    interactive=False, bootstrap_seconds=60, request_seconds=30,
    samples=1, sample_interval=0,
    snapshot_validator=parse_candidate_response,
    on_snapshot=received.append,
)
# Browser cleanup has completed here. Later ingestion may assess/store received[0].
```

The snippet assumes imports from `provadis_m1.browser` and
`provadis_m1.validation`, and an already locked profile path. It is not a new CLI
or a reason to use an ongoing M1 acceptance profile.

The callback receives no bearer, headers, raw envelope or private metadata. It
should capture in memory and return promptly; later persistence belongs after
browser cleanup. Callback errors propagate and close the context. They occur
outside authentication recovery, so even an `AuthenticationError` raised by a
consumer cannot trigger another login or GET. Request retries, deadlines,
redirect restrictions and one bounded HTTP-401 recovery remain unchanged.

`browser_run` still returns diagnostics and event counts only. M1's CLI does not
enable the new options or serialize candidates. Existing `verify` behavior is
unchanged and does not create storage observations.

## Corrected assessment requirements for Issue #5 (not implemented here)

A candidate is structurally valid data, **not an accepted snapshot**. The empty
candidate is representable so Issue #5 can quarantine it; M1's ordinary empty
response still exits with its existing suspicious-response outcome.

The approved next increment must use these corrections to Phase A:

- Empty responses remain suspicious by default; repetition does not establish trust.
- Large growth alone must not trigger quarantine. Publishing a whole next semester
  is normally acceptable when otherwise trustworthy; growth may be informational.
- Completed lectures naturally disappear. Compare previous/candidate future counts
  at the same candidate receipt time using `end > reference_time`, including row
  multiplicity. Do not compare raw total counts as a cancellation signal.
- Remaining future-decrease thresholds must be explicit, configurable, documented
  and boundary-tested. No fixed semester dates or maintained semester calendar.
- No count change itself implements cancellation; identities/counters remain M3.
- Tests in #5 must simulate normal semester progression and a large new-semester
  publication. These assessment behaviors are not claimed as implemented by #4.

## Environment and tests

No dependency was added. Python 3.12.14 and the existing locked Playwright 1.62.0
environment suffice. SQLite will use Python's standard library in #6 onward;
no server, Docker or ORM is required. Verify its linked version with:

```sh
.venv/bin/python -c 'import sqlite3; print(sqlite3.sqlite_version)'
```

Database initialization/schema-inspection commands will accompany #6, when they
exist; do not create a throwaway production database during this increment.

From the M2 checkout on macOS or its PyCharm terminal:

```sh
cd "$HOME/Downloads/otter-m2"
.venv/bin/python --version
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_m2_*.py' -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
OTTER_BROWSER_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover \
  -s tests -p test_auth_browser.py -v
.venv/bin/python -m compileall -q src tests scripts
sh -n scripts/setup scripts/m1 scripts/m1-acceptance .githooks/pre-commit
git diff --check
```

New tests reuse the existing synthetic API and HTML fixture structures. They
exercise field projection/privacy, optional locations, Unicode, old events,
immutable records, pre-epoch/fractional-second arithmetic, source offsets/DST,
whole-response rejection and empty/duplicate preservation. HTML-backed mocked
transport exercises the actual browser-run/direct-fetch/parser path, including
401 recovery, response disposal, consumer failures and transient transport retry.
It does not emulate Microsoft's JavaScript or prove live authentication.

In PyCharm select M2's `.venv/bin/python`, `tests` as the Unittests target, M2
as working directory and `PYTHONPATH=src`. Use a separate configuration with
`OTTER_BROWSER_TESTS=1` for Chromium fixtures. No credentials are needed.

On Ubuntu, in its isolated M2 test checkout, apply the same patch and use:

```sh
uv sync --locked --no-build
.venv/bin/python --version
.venv/bin/python -c 'import sqlite3; print(sqlite3.sqlite_version)'
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_m2_*.py' -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m playwright install --with-deps chromium
OTTER_BROWSER_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover \
  -s tests -p test_auth_browser.py -v
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

Browser installation may require host administrator access. `OK`/exit 0 for the
offline suite establishes local regression checks only. A launch error is not a
passed/skipped browser test. Stop on assertion failures or apply conflicts;
do not relax validators or remove Chromium sandboxing to obtain a pass.

### Verification during patch preparation

On macOS arm64 using Python 3.12.14 / Playwright 1.62.0 / SQLite 3.53.1:

- 23 new candidate/handoff tests passed without live services.
- Full offline suite: 202 discovered, 194 passed, eight opt-in browser tests
  skipped; exit 0.
- Explicit Chromium suite: eight attempted, eight startup errors
  (`TargetClosedError`, Chromium aborted with SIGABRT); exit 1. These tests did
  not exercise fixture assertions successfully in the Work environment.
- Compilation, shell syntax, whitespace and staged publication checks passed.
- No live Microsoft/Provadis login or Ubuntu execution was performed. Local
  Chromium regression checks remain required where the browser can launch.

Final patch application/reversal and exact-content comparison are checked in a
separate disposable checkout. The owner's M1/M2 worktrees remain unchanged.

## Apply, review, commit and reverse

Use only the existing M2 worktree/branch; no original M1 files or profiles need
to change. A patch changes files without committing/pushing/deploying them.

```sh
cd "$HOME/Downloads/otter-m2"
git status --short --branch
git rev-parse HEAD
patch_file="$HOME/Downloads/OTTER_M2_Issue_4_Candidates.patch"
git apply --stat "$patch_file"
less "$patch_file"
git apply --check "$patch_file"
git apply "$patch_file"
git diff --check
```

Require the documented base and a clean tree. `--check` changes no files; do not
use `--reject` for partial installation. After the tests above, stage only:

```sh
git add -- src/otter/__init__.py src/otter/models.py src/otter/normalization.py \
  src/provadis_m1/validation.py src/provadis_m1/browser.py \
  tests/test_auth_runner.py tests/test_m2_candidates.py tests/test_m2_handoff.py \
  docs/m2-candidates.md
git diff --cached --check
git diff --cached
.venv/bin/python scripts/check_public_tree.py
git commit -m "feat(validation): expose normalized timetable candidates" -m "Refs #4"
```

Staging selects the commit contents; the publication guard scans the index,
including staged new files. `Refs #4` links progress without closing the Issue.
No push or PR is part of this increment. Never copy credentials/profile contents
or real API responses into tests or Git.

Before commit and without overlapping later edits, reverse with:

```sh
git apply --reverse --check "$patch_file"
git apply --reverse "$patch_file"
```

If already staged, first unstage just the nine paths above using
`git restore --staged -- <those paths>`; this preserves files for patch reversal.
After committing, use a reviewed `git revert <commit-sha>` to preserve history.
Do not use reset-hard or git-clean shortcuts. There is no database migration to
reverse in this increment. Share test results/review before starting Issue #5.
