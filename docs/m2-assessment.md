# M2 increment B — snapshot assessment and content identity (Issue #5)

Base: `c8c7f1557c8d970c642378314bd12d0647ad1eae`, the owner's committed Issue #4.
Prerequisite: increment A. This patch is incremental; do not reapply Issue #4.
It implements [Issue #5](https://github.com/JJandke/otter/issues/5) in the existing
**M2 – Validation & Storage** milestone. No Issues/PRs/commits were created or
edited, and no original worktree, authentication profile or service was modified.

## Scope and API

`otter.assessment.assess_snapshot(candidate, received_at=..., baseline=..., policy=...)`
is pure: it reads no clock and performs no network, filesystem or database work.
Input must be a structurally validated `SnapshotCandidate` from the Issue A
parser, not an unvalidated dictionary or manually forged event records.

The result is an immutable `SnapshotAssessment` with an `accepted` or
`suspicious` recommendation, all applicable fixed reason codes, informational
codes, safe counts, exact policy settings and a normalized-content fingerprint.
This is not a committed observation and cannot move a database pointer. Invalid
HTTP/API data is still rejected by the existing parser before assessment.

An optional `AcceptedBaseline(candidate, received_at)` describes the last
accepted observation. The caller must enforce its provenance. Never replace it
with a failed, suspicious or unreviewed candidate. Storage/ingestion will enforce
that rule in Issues #6/#7. With no baseline, any nonempty duplicate-free valid
candidate is bootstrap data, including an old-only snapshot. No date cutoff,
semester calendar, calendar filtering or cancellation algorithm is introduced.

Example, assuming `candidate` and `previous_candidate` came from the parser:

```python
from datetime import datetime, timezone
from otter.assessment import AcceptedBaseline, AssessmentPolicy, assess_snapshot

result = assess_snapshot(
    candidate,
    received_at=datetime(2027, 6, 1, 12, tzinfo=timezone.utc),
    baseline=AcceptedBaseline(
        previous_candidate, datetime(2027, 6, 1, 11, tzinfo=timezone.utc)
    ),
    policy=AssessmentPolicy(minimum_future_drop=5, future_drop_percent=30),
)
safe_summary = result.safe_diagnostics()
```

These dates are illustrative inputs, not semester boundaries. Real callers must
capture each response's actual receipt time. Baseline and candidate receipt times
must be offset-aware and representable in UTC; invalid local configuration raises
a fixed ValueError/TypeError, never a trusted assessment. Equivalent timezone
representations of a receipt instant yield the same result.

## Semester-aware decrease policy, version 1

Let `t` be the candidate receipt instant. Recalculate both counts at **that same
instant**:

- `B`: baseline rows whose end is later than `t`.
- `C`: candidate rows whose end is later than `t`.
- `D = B - C`: net decrease in unfinished rows.

An in-progress lecture is included; one ending exactly at `t` is completed and
excluded. This is an anomaly metric only: historical rows remain unchanged in
the candidate and fingerprint, even when omitted from these counts.

Default future-decrease quarantine requires **both** `D >= 5` and `100*D > 30*B`.
The absolute boundary is inclusive and the percentage boundary is strict. All
comparisons use integer arithmetic, avoiding floating-point rounding at 30%.
There is no division when `B=0`.

Configure `minimum_future_drop` as an integer >=1 and `future_drop_percent` as an
integer from 0 through 100. Booleans, floats, NaN, strings and invalid ranges are
rejected. At 100%, count-drop quarantine is disabled; empty/duplicate/clock gates
remain active. A caller's exact chosen values and policy version are included
in safe diagnostics, ready for later persistence. CLI/config-file wiring belongs
to Issue #8; there is no new command-line configuration in this increment.

| Scenario | Result |
|---|---|
| 70 total rows become 10 after 60 lectures finish; both have 10 unfinished at `t` | Accepted |
| 10 unfinished rows become 260 when the next semester is published | Accepted; growth is informational |
| Zero unfinished baseline rows become 250 | Accepted; growth is informational |
| 20 unfinished rows become 14 | Accepted: exactly 30% decrease |
| 20 unfinished rows become 13 | Suspicious: decrease 7 and 35% |
| 10 unfinished rows become 6 | Accepted: absolute decrease only 4 |
| Nonempty old-only snapshot | Accepted if otherwise valid and anomaly-free |
| Empty snapshot, even after all previous lectures have finished | Suspicious by default |

Large growth alone is never quarantined. Additional historical rows can increase
the total while unfinished rows decrease; the future-decrease check still applies.
These are net-count comparisons, not event matching. Added future rows can mask
omissions, and equal-size replacement/partial data can evade this policy. Count
validation cannot establish API completeness, scope or identity. This limitation
must remain explicit when M3 cancellation processing is designed.

## Other gates and safe diagnostics

All applicable reasons are retained in deterministic order:

1. `empty_timetable`: zero event rows, including bootstrap or repeated empties.
2. `duplicate_events`: exact normalized row duplicates; multiplicity is preserved.
3. `observation_time_regressed`: receipt precedes the last accepted receipt.
4. `unexpected_future_decrease`: both configured decrease conditions hold.

Equal receipt times are allowed; observation IDs/idempotency remain Issue #7.
There is no auto-promotion after repeated suspicious observations. Explicit review
and its freshness/baseline checks remain Issue #7, not a bypass in this module.

`duplicate_excess_count` counts copies beyond the first: a triple row contributes
two. It includes historical duplicates too. Same-title lectures at different
times remain distinct. Building/room changes alone are valid. No known location
is still `None`; no Online/Teams label is invented. Growth cannot override another
gate. First snapshots receive no special rejection based on their age.

`safe_diagnostics()` exposes only fixed codes, counts, version/threshold values
and the processing reference instant. No titles, locations, raw JSON, tokens,
emails, person IDs, arbitrary exception messages or content digest appear there
or in normal result reprs. Deliberate full dataclass serialization is not the
logging interface; normalized timetable contents and hashes should remain private.

## Deduplicated content identity

`otter.normalization.canonical_snapshot_bytes(candidate)` produces a private UTF-8
JSON object. It contains `format_version=1`, the candidate's normalization version
and a sorted list of rows. Each row is:

`[title, start_utc_us, end_utc_us, start_offset_seconds, end_offset_seconds, building, room]`

Rows are sorted by their compact JSON encodings; object keys use sorted order,
compact separators and literal Unicode. This avoids comparing `None` directly
with strings and is independent of source ordering. Exact encoding is locked by
a golden-byte test. All duplicate copies are retained. Changing the encoding
contract requires a new format version; changes in normalization require the
appropriate normalization version as well.

`fingerprint_snapshot(candidate)` returns a frozen `SnapshotFingerprint` with
SHA-256, normalization/format version and row count. Reordered identical contents
share the fingerprint. Duplicate multiplicity and every retained field affect it,
including the original offsets. Fetch times, policy decisions and discarded API
metadata do not. Empty content has an identity without acquiring trust.

This is snapshot identity only. It supplies no event UID, event matching or
cancellation state. Later storage must verify normalized rows/count/version on
a digest conflict instead of overwriting stored content.

## Tests and commands

New tests cover semester progression/publication, old-only data, missing locations,
absolute/relative boundaries and configuration, empty/duplicate/clock gates,
in-progress/end-at-reference handling, deterministic privacy-safe results and
content canonicalization/multiplicity. A malformed-response test confirms parser
rejection without mutating the in-memory baseline; database preservation will be
tested with actual SQLite in later increments.

macOS/PyCharm terminal, from the existing M2 worktree after application:

```sh
cd "$HOME/Downloads/otter-m2"
.venv/bin/python --version
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_m2_assessment.py -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_m2_fingerprints.py -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

PyCharm: M2's `.venv/bin/python`, M2 working directory, Unittests targeting `tests`,
`PYTHONPATH=src`. No credentials are needed. On the isolated Ubuntu M2 checkout,
use the same commands after `uv sync --locked --no-build` if environment setup
is needed. No dependencies or M1 source files changed. SQLite is not used yet;
database initialization/schema commands will accompany Issue #6.

The expected new checks are 23 assessment and eight fingerprint tests. Standard
discovery should find 233 tests, with eight Chromium fixture skips by default.
To run those separately where Chromium launches:

```sh
OTTER_BROWSER_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover \
  -s tests -p test_auth_browser.py -v
```

No new 25/26-hour run is required for these pure data-layer changes. The owner
reported all Issue A tests passed on macOS and Ubuntu; that is separate from this
increment's preparation results. Stop on assertion failures or changed test counts
that cannot be explained by other reviewed changes; do not change policy merely
to force a pass. CLI behavior remains unchanged until explicit M2 integration.

### Actual preparation results

On macOS arm64, Python 3.12.14 / Playwright 1.62.0 / SQLite 3.53.1:

- 31 new tests passed (23 assessment, eight fingerprint).
- Full offline discovery: 233 discovered, 225 passed, eight browser skips; exit 0.
- Explicit Chromium fixtures: eight attempted, eight startup errors with
  `TargetClosedError` / Chromium SIGABRT; exit 1. They did not pass in Work.
- Compilation, shell syntax, whitespace and staged publication checks passed.
- No live authentication or Ubuntu execution was performed for this increment.

The final patch is applied and reversed in a second disposable checkout, with
exact Git content/mode comparison and an offline suite run on the applied files.
Owner-run Chromium checks remain separate from these preparation results.

## Apply, commit and reverse

Require the exact base and a clean M2 branch. Do not switch the original M1 checkout:

```sh
cd "$HOME/Downloads/otter-m2"
git status --short --branch
git rev-parse HEAD
patch_file="$HOME/Downloads/OTTER_M2_Issue_5_Assessment.patch"
git apply --stat "$patch_file"
less "$patch_file"
git apply --check "$patch_file"
git apply "$patch_file"
git diff --check
```

`--check` changes no files. Application makes no commit; stop on conflicts rather
than using `--reject`. After successful tests, stage only these seven paths:

```sh
git add -- src/otter/models.py src/otter/normalization.py src/otter/assessment.py \
  tests/test_m2_assessment.py tests/test_m2_fingerprints.py \
  docs/m2-assessment.md docs/m2-candidates.md
git diff --cached --check
git diff --cached
.venv/bin/python scripts/check_public_tree.py
git commit -m "feat(validation): assess snapshot trust and fingerprint contents" -m "Refs #5"
```

Staging chooses commit contents; the guard checks indexed new files too. `Refs #5`
links progress without closing the Issue. No push, PR, merge or deployment occurs.

Before commit, without overlapping later edits:

```sh
git apply --reverse --check "$patch_file"
git apply --reverse "$patch_file"
```

If staged, unstage only the seven paths first (`git restore --staged -- <paths>`).
After commit, use a reviewed `git revert <commit-sha>`. Do not reset-hard or clean
private state. The next increment after owner review/tests is Issue #6 (SQLite),
not additional policy/identity work. There is no database migration to undo here.
