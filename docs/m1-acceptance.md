# M1 acceptance runner and unattended authentication

## Current scope and evidence — 25 November 2026

This increment is based on clean `fix/m1-session-diagnostics`, commit
`7bf64f34578a7bc967952c3f0d4e6916159939a2`. The original checkout and profiles
were not modified. No commits, pushes, branch operations or live account access
were performed by the assistant.

The owner reports that all existing tests, logins and verify commands succeeded
on Mac and Ubuntu after the SSO-return fix. The owner also reports a successful
longtime-test rerun approximately 24 hours later. These are positive owner-reported
results; this task did not independently execute or inspect those live runs.
The host/runtime/precise elapsed time and intervening activity for the 24-hour
result were not supplied. Do not silently call it a >24-hour boundary test, a
25-hour idle test or evidence that a password was resubmitted.

The owner now requires unattended reauthentication: occasional manual login is
not an acceptable normal operating policy. Earlier documents suggesting manual
reauthentication could be accepted are superseded by this document.

## Features supplied by this patch

1. `fetch` and `verify` accept explicit `--credentials-file` and optional
   `--decline-passkey`. They reuse the existing shared state detector: a healthy
   session skips credential forms; required password forms are filled without
   operator interaction. This uses normal portal navigation, not a token-cache
   reader or an independent OAuth implementation.
2. Existing budgets remain: one HTTP-401 navigation recovery, at most one email
   and password submission per browser run, finite state/global deadlines and no
   automatic HTTP-403 login loop. Wrong credentials and unknown prompts fail
   nonzero rather than repeating password attempts indefinitely.
3. Summaries report `credential_submission_used`. `verify` reports
   `restart_reuse_demonstrated: false` if either browser lifetime submitted
   credentials. Successful retrieval is distinct from pure persistent-session reuse.
4. `python -m provadis_m1.acceptance` and `scripts/m1-acceptance` provide finite,
   explicit live smoke, active and idle tests on macOS/Linux.
5. Private reports contain selected counts, state/action evidence, durations,
   OS/architecture, Python/Playwright versions, Git revision and a source hash.
   Child stderr is discarded; raw child stdout is parsed/projected and never
   saved. Arbitrary child fields/messages are excluded. Reports/profiles are
   ignored by Git and blocked by the publication guard if force-staged.

No credential file is searched for implicitly. Pass the existing private `.env`
file explicitly; it must be outside the repository, owner-only (0600), and contain
`OTTER_USERNAME` and `OTTER_PASSWORD` using the existing loader's format. It is
read as data, never sourced. Paths are passed to children, not password values.

Supplying a credential file authorizes automatic credential use when needed; it
is not an instruction to type the password on every run. The default behavior
without that option remains a strict session-reuse check.

The existing opt-in passkey-refusal behavior is unchanged. The runner examples
include `--decline-passkey` for the captured password workflow. No MFA, CAPTCHA,
consent or security-policy bypass is implemented. If the live tenant requires an
interactive challenge that passwords cannot satisfy, fully unattended operation
is not established and M1 remains open until a supported solution is found.

## Runner modes and report interpretation

| Mode | Finite sequence | What a pass establishes |
| --- | --- | --- |
| smoke | Fresh headless credential-assisted login; three separate credential-free wet fetches; credential-free verify; another fresh profile with automatic credential-assisted fetch; credential-free verify | Fresh credential entry, strict short-term reuse and cold-fetch automatic login work without interaction |
| active | Fresh automatic fetch baseline; strict verify; fresh fetch processes at configured intervals through the configured horizon; final credential-enabled verify | Unattended retrieval over the measured active duration; report distinguishes fresh credential submissions |
| idle | Fresh automatic baseline; strict verify; no browser activity during the requested idle period; automatic fetch; final verify | Unattended retrieval/recovery after idle, with `idle_reuse_without_credentials` recording whether credentials were needed |

Default smoke uses two fresh isolated profiles, not the owner's existing profile.
`--fresh-cycles 2` repeats the entire smoke sequence with new profiles, and
`--wet-runs` changes its wet-fetch count. Limits are 1–5 cycles and 1–20 wet runs.
Fresh phases require an observed password submission; a successful response with
no such evidence is reported **inconclusive**, not proof the credential file worked.
The runner does not intentionally revoke sessions, submit wrong passwords or
manipulate cookies. Cold-start tests do not replace live natural-expiry testing.

Every application command is a separate process using the runner's exact Python
interpreter. `verify` additionally creates its existing two browser lifetimes.
No credential-free probe is allowed to quietly reauthenticate. Active/idle
recovery probes explicitly authorize credentials, satisfying the chosen policy.
Their success can establish recovery without establishing cookie survival.

Reports live under `.m1-acceptance/run-<UTC>-<unique>/report.json`, with profiles
and application diagnostics in dedicated subdirectories. The parent must be named
`.m1-acceptance`; an alternative parent path can be supplied with `--output-dir`.
The runner creates unique directories and never selects, overwrites or deletes
existing profiles. All state remains private (0700 directories, 0600 reports).

A report is written atomically before/after steps and at completion. While waiting,
its `waiting` object records the requested wait and not-before UTC time. Failed
credential attempts are retained as sanitized action counts too; missing failure
metadata is marked with `credential_evidence_complete: false`. Exit codes:

| Exit | Meaning |
| --- | --- |
| 0 | Requested finite test passed |
| 1 | Child failed or acceptance condition failed |
| 5 | Inconclusive: missing fresh-password evidence or active schedule gaps |
| 6 | Local setup/runner error |
| 130 | Operator interrupted; no pass claimed |

The report stores each child exit/category and allowlisted reason/state fields.
An unrecognised reason becomes `unclassified_child_failure`, never copied raw.
An unexpected summary or a success without validated sample evidence fails the
protocol check. API event contents are never copied into runner reports.

Child watchdogs derive finite limits from bootstrap budgets and browser lifetimes.
Timeout or interruption terminates the child's private process group. The runner
stops on the first unexpected failure; it does not install service retries or a
production scheduler. Application navigation retries remain bounded as before.

During waiting periods, the runner holds the application's profile lock. Other
OTTER commands cannot silently refresh that test profile. Direct browser access
outside OTTER is unsupported; do not touch the idle profile manually. Each mode
uses different new profiles, so an idle test is not kept alive by its active test.

Active samples use start-to-start intervals. Missed slots are skipped, never
replayed in a burst. A final fresh fetch starts at or after the requested horizon.
Large timing gaps are recorded and make the active test inconclusive. Both wall
and monotonic elapsed times are recorded; significant backwards clock movement
fails timing acceptance. A source-code/dependency-file change during a run fails
acceptance instead of mixing implementations.

The runner stays in the foreground. Reports survive interruption, but this
version does not resume interrupted schedules. A fresh invocation starts new
profiles. Keep the process/host alive for active tests; machine sleep can create
an inconclusive schedule gap. These tests are not autostart services.

## Apply, branch choice and rollback

Save the attachment as `~/Downloads/OTTER_M1_Acceptance_Runner.patch`.
The current branch is already the M1 branch, so no extra branch is required.
Use it for the simplest workflow:

```sh
cd /Users/Johannes/Downloads/otter
git status --short
git branch --show-current
git rev-parse HEAD
acceptance_patch="$HOME/Downloads/OTTER_M1_Acceptance_Runner.patch"
git apply --stat "$acceptance_patch"
less "$acceptance_patch"
git apply --check "$acceptance_patch"
git apply "$acceptance_patch"
git diff --check
git status --short
```

`--check` changes nothing; `git apply` makes no commit. Review overlapping changes
before applying; do not partially install with `--reject`.

For separate feature review, optionally create a branch **before** applying:

```sh
git switch fix/m1-session-diagnostics
git switch -c feat/m1-acceptance-runner
```

That feature branch starts from the inspected M1 implementation, not from `main`.
Its PR should target `fix/m1-session-diagnostics`. The final M1 PR then targets
`main`. No branch creation or remote operation was performed automatically.

Before committing, reverse an otherwise unmodified application with:

```sh
git apply --reverse --check "$acceptance_patch"
git apply --reverse "$acceptance_patch"
```

This does not remove private test state. Overlapping or staged edits need review
first. After committing, prefer a reviewed `git revert <commit-sha>`; avoid
reset-hard/git-clean shortcuts that could discard other work.

## Exact test commands

Select the interpreter explicitly. On the current Mac, preserve the existing 3.14
venv and use the already-created separate 3.12 environment:

```sh
m1_python=/tmp/otter-m1-python312/bin/python
"$m1_python" --version
```

On Ubuntu, from its test checkout, use the configured venv after confirming it is
Python 3.12.14:

```sh
m1_python="$PWD/.venv/bin/python"
"$m1_python" --version
```

Do not replace an environment/profile midway through an existing longevity series.
The shell wrapper uses `.venv/bin/python`; module commands below use the explicitly
selected interpreter. The report records a declared-runtime-match flag.

### Offline tests and browser fixtures

```sh
PYTHONPATH=src "$m1_python" -m unittest discover -s tests -v
OTTER_BROWSER_TESTS=1 PYTHONPATH=src "$m1_python" -m unittest discover \
  -s tests -p test_auth_browser.py -v
"$m1_python" -m compileall -q src tests scripts
sh -n scripts/m1 scripts/m1-acceptance scripts/setup .githooks/pre-commit
```

### New live smoke acceptance — run separately on Mac and Ubuntu

Use your existing credentials file; do not paste values into commands or reports:

```sh
credentials_file="$HOME/.config/otter/credentials.env"
PYTHONPATH=src "$m1_python" -m provadis_m1.acceptance smoke \
  --credentials-file "$credentials_file" --decline-passkey
```

To repeat fresh cases and increase wet runs:

```sh
PYTHONPATH=src "$m1_python" -m provadis_m1.acceptance smoke \
  --credentials-file "$credentials_file" --decline-passkey \
  --fresh-cycles 2 --wet-runs 5
```

These are real university sign-ins/API requests when run by the owner. They were
not run using the owner's account by the assistant. Stop and inspect the first
failure; do not repeatedly rerun a failed credential case without diagnosis.

### Active longevity — target Ubuntu host first

```sh
PYTHONPATH=src "$m1_python" -m provadis_m1.acceptance active \
  --credentials-file "$credentials_file" --decline-passkey \
  --duration-hours 26 --interval-seconds 3600
```

The 26-hour horizon deliberately tests beyond 24 hours. It starts a new measured
series and does not overwrite or relabel the owner's earlier successful series.
Longer 48/72-hour or multi-day runs can increase confidence; short success never
proves indefinite renewal. Production cron/systemd scheduling remains later work.

### Idle recovery — separate untouched profile

```sh
PYTHONPATH=src "$m1_python" -m provadis_m1.acceptance idle \
  --credentials-file "$credentials_file" --decline-passkey --idle-hours 25
```

The runner owns a new idle profile and makes no application calls during the wait.
Success after automatic credential submission satisfies unattended recovery, but
`idle_reuse_without_credentials: false` means it did not prove cookie-only reuse.

### Individual credential-enabled operations

For an existing dedicated state, these now automatically handle supported login
forms when required:

```sh
PYTHONPATH=src "$m1_python" -m provadis_m1 fetch \
  --state-dir "$otter_test_state" --credentials-file "$credentials_file" \
  --decline-passkey --navigation-log
PYTHONPATH=src "$m1_python" -m provadis_m1 verify \
  --state-dir "$otter_test_state" --credentials-file "$credentials_file" \
  --decline-passkey --navigation-log
```

Without `--credentials-file`, these remain strict session-reuse tests. Restore
the actual state-directory variable in a new Terminal before running them; do
not accidentally default to another profile. Ordinary application commands still
validate the exact API response before reporting success.

## Everything remaining for M1

| Area | Development status after applying this patch | Remaining acceptance |
| --- | --- | --- |
| Fresh/wet state navigation | Implemented, owner previously reports Mac/Ubuntu success | New smoke reports on the final source/runtime |
| Automatic password-based authentication/recovery | Explicitly exposed for fetch/verify by this patch | Genuine headless fresh fetch and naturally expired-session recovery without interaction |
| Portable acceptance runner | Implemented by this patch | Run smoke/active/idle on actual hosts; review private reports |
| Error, secret and concurrency handling | Implemented with offline regression coverage | Inspect new real failure artifacts if any; confirm clean publication review |
| Runtime and installation | Python 3.12.14 and locked stack declared | Record exact host/runtime versions and green CI for the final commit |
| Longevity | Owner reports successful rerun after about 24 h | Controlled >24 h active series and separate >=25 h idle series |
| Documentation/evidence | Updated status, policy and checklist supplied | Attach sanitized final outcomes to relevant Issues; resolve any observed failures |
| Normal operation without manual login | Required, not optional | Remains blocked if tenant demands unsupported interactive challenges during normal renewal |

No further broad M1 feature is planned if these tests pass. Any newly observed
failure still needs a targeted fix and regression test. The runner intentionally
has no production daemon, alerting service, SQLite, ICS generation or deployment.
Those belong to later milestones. Its finite test intervals are implemented now.

## Complete M1 test checklist

| Test | Where/how | Gate |
| --- | --- | --- |
| Locked clean install and Python 3.12.14 metadata | Both hosts; existing setup/CI instructions | Required |
| Offline suite, including new CLI/runner regressions | Both hosts; unittest discovery | Required |
| All six Chromium selector/navigation fixtures | Both hosts; explicit browser suite, no live service | Required |
| Fresh headless explicit login with actual password submission | Smoke; new profile | Required |
| Fresh `fetch --credentials-file` automatically logs in | Smoke; second new profile | Required |
| Several wet fetches with no credential submission | Smoke; same profile, separate processes, no file supplied | Required |
| Strict two-browser verify with at least two valid direct samples each | Smoke; credentials absent | Required |
| Expired session automatically recovers using configured file | Offline 401 regressions plus natural live expiry | Required; cold start alone is not expiry proof |
| Repeated 401, 403, wrong credentials, unknown prompts and timeouts | Existing offline/mocked tests; no deliberate bad-password live test | Required regression gate |
| Status/content/schema/count/date/body-limit and empty-response handling | Offline synthetic API fixtures | Required regression gate |
| Credential-file ownership/path and log/report redaction | Offline tests and review of final generated reports | Required |
| Profile concurrency and idle lock | Offline tests; dedicated test state only | Required |
| Parent timeout/process cleanup, Ctrl+C and partial report behavior | Offline synthetic subprocess tests | Required |
| Clock rollback, missed slots, source changes and no catch-up bursts | Offline virtual clocks and report evidence | Required |
| Active retrieval beyond 24 h, final fresh process after horizon | Target Ubuntu; 26 h example above | Required |
| >=25 h profile idle, followed by unattended retrieval/recovery | Target Ubuntu; separate series | Required |
| No actual interactive login needed during renewal | Target Ubuntu live recovery evidence | Required by current owner policy |
| Clean staged private-artifact scan and green CI | Final M1 source | Required |
| 48/72 h and 7/14/30-day observations | Additional finite active runs | Extended confidence |
| OS reboot and deliberate account-session revocation | Separate explicit operator-controlled experiment | Optional, never performed automatically |

A valid empty timetable currently remains inconclusive under the existing M1
validator, not proof of authentication failure. If that occurs in live acceptance,
review the policy deliberately rather than treating an empty response as a pass.

## Verification of this patch by the assistant

The new runner tests use synthetic child summaries, harmless local Python
subprocesses and virtual clocks. They do not wait 25 hours or log into the
university. The existing auth/API regressions remain in the suite.

- Python 3.12.14 / Playwright 1.62.0: 154 discovered, 148 passed, six browser skips.
- Existing Python 3.14.7 environment: the same result.
- Explicit browser fixture run: all six stopped before page creation with
  `TargetClosedError` from Chromium startup in this execution environment.
- Compileall, shell syntax and the existing staged publication guard passed.
- Forward/reverse patch application and full content/Git-mode comparisons passed.
- Read-only check against the actual checkout passed; all original tracked files
  remained unchanged. No commits, pushes or live account tests were performed.

These checks verify the implementation/protocol and simulated timing cases, not
the live acceptance of new runner modes or new credential-enabled fetch behavior.
The owner's prior live results remain separately attributed above.

## Commit and merge

Keep the existing M1 branch unless you chose the optional feature branch. Stage
only the patch's reviewed files; the new report/profile tree must stay unstaged:

```sh
git add -- .gitignore .github/workflows/tests.yml README.md \
  docs/m1-acceptance.md docs/feasibility.md docs/authentication.md \
  src/provadis_m1/__main__.py src/provadis_m1/acceptance.py \
  scripts/m1-acceptance scripts/check_public_tree.py \
  tests/test_cli.py tests/test_acceptance.py
git diff --cached --check
git diff --cached
"$m1_python" scripts/check_public_tree.py
git commit -m "feat(auth): add M1 acceptance runner and unattended reauthentication" \
  -m "Refs #ISSUE_NUMBER"
```

Replace `ISSUE_NUMBER` with the existing relevant Issue. A separate Issue such as
"M1: verify unattended authentication and active/idle acceptance" can track the
remaining live gates. Use `Refs` while results are pending; use `Closes` in the
reviewed PR only when its actual acceptance criteria pass. No GitHub changes were
made by this task. Merge the M1 branch to `main` after required gates pass, not
merely because the new code or old 24-hour test succeeded.
