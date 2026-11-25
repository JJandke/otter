# Detached credential control: recovery and evidence

## Base and diagnosis

This incremental patch is based on clean `fix/m1-session-diagnostics`, commit
`bd82d19ffe3662d5fd93305361a8b485e636c445`. It includes the acceptance-runner
increment already present at that revision; do not reapply the earlier patch.
The original checkout, profiles and credentials were not modified.

The supplied failed smoke report shows `password` / `click_control` /
`element_detached`. The old implementation pinned one submit element and let
its detached-handle error abort authentication. Replacement during rendering is
consistent with that evidence; the report alone cannot identify Microsoft's
specific JavaScript mutation. An offline reproduction replaces the button at
click time: the original code fails, while the corrected code reacquires it and
performs one actual click under one credential-attempt budget.

There is a separate reporting defect: `auth_actions.password == 1` was treated
as an observed submission. That counter increments before fill/click. The
supplied `password_submission_observed: true` therefore does not prove a POST
or an accepted password. Existing successful API validation and the owner's
24-hour result remain useful evidence, but historical submission flags cannot
be repaired from attempt counters alone.

## Recovery rules

Email and password share one bounded transaction. The captured original email
and password documents both contain `form#i0281[name=f1]`, method POST, with
action origin/path `https://login.microsoftonline.com/common/login`. Their
sanitized fixtures now retain that form relationship and public action, without
hidden inputs, captured values, action parameters or session state.

Each retry checks the current HTTPS origin, the uniquely visible captured input
selector, its POST form and trusted action, and that the submit button belongs
to that same active form. No credentials are filled on another origin. A pinned
element handle still protects against locator re-resolution across documents.

A listener installed before Microsoft page scripts records activation
(`pointerdown`, `mousedown`, `click`) and submit dispatch as booleans. It reads no
credential values and changes no events. A detached control may be reacquired
only when this document-local evidence establishes no dispatch. There are at
most three reacquisitions within the original UI-operation deadline; neither
the timeout nor the once-per-email/password attempt budget resets. No sleeps or
unbounded click loops are introduced.

If activation/submission was dispatched or document evidence was lost after
preparation, the outcome is uncertain. OTTER re-detects state and waits for the
existing state/global deadline. It never blindly submits again. A later matching
request can resolve the evidence. Rejected credentials, return to a previously
completed credential phase, or a second attempt after explicit expiry recovery
still fail within existing limits. A future independent invocation may use the
explicit credential file again; this patch introduces no automatic process loop.

Only a POST matching the armed form's exact action and page main frame sets the
submission observation. OTTER examines method, URL and frame metadata; it does
not inspect request bodies, headers or password values. The action is transient
private memory, excluded from dataclass representations and diagnostics. When
the detector advances to another meaningful state, the previous phase stops
accepting observations so another form POST cannot be credited to it.

## Report interpretation

Acceptance reports use schema **2**. New fields are allowlisted/projected; raw
errors, credentials and form-action URLs are never copied into reports.

| Field | Meaning |
| --- | --- |
| `auth_actions.email/password` | Logical action attempts, including incomplete actions |
| `credential_attempted` | Any credential phase was attempted |
| `credential_submissions_observed` | Per-phase 0/1: a matching form POST was observed |
| `credential_submission_outcomes` | `not_started`, `prepared`, `awaiting_request`, `uncertain`, or `request_observed` |
| `credential_control_recoveries` | Per-phase bounded reacquisition count, 0–3 |
| `credential_submission_used` | At least one matching credential-form POST was observed |
| `password_submission_observed` | Specifically the password-phase POST was observed |
| `credential_evidence_complete` | Explicit evidence exists for every run; every attempted credential phase has a matching POST |
| `credential_attempting_steps` / `credential_submitting_steps` | Separate attempt and observed-submission step totals |

An observed POST does not establish what the body contained, credential
acceptance, successful MFA, or API access. Success still requires validated
timetable API data. A click with no POST is `awaiting_request`; a detached action
after dispatch/document loss is `uncertain`. Neither claims observed submission.
Fresh smoke phases with validated API data but no password POST are
**inconclusive**, rather than evidence that the password file worked. Missing
legacy fields yield incomplete evidence, never inferred positive observations.
For example, Microsoft's email discovery may progress without a form POST;
email evidence then remains incomplete even if password submission is observed.

`verify.restart_reuse_demonstrated` is false if credentials were attempted,
including uncertain actions with no observed POST. Strict wet probes still
receive no credential file. New navigation events are
`credential_control_reacquired`, `credential_submission_uncertain` and
`credential_request_observed`; they contain fixed phase labels only.

## Verification of this patch

- Python 3.12.14 / Playwright 1.62.0 offline suite: 173 discovered, 165 passed,
  8 opt-in browser tests skipped. Also passed with the locally available Python
  3.14.7 as a compatibility check; 3.12.14 remains the declared runtime.
- Replaced-button reproduction: baseline fails with detachment and zero clicks;
  patched version makes one click under one logical password attempt.
- Offline tests cover pre-dispatch replacement, dispatch-then-error, document
  loss, origin/form changes, finite retry/time budgets, no second credential
  attempt after expiry, matching-request attribution, delayed evidence, and
  corrected acceptance/CLI projections. An HTML-backed mocked driver regression
  continues from a replaced password button to validated synthetic API data.
- The schema-2 smoke runner is covered with synthetic child summaries and a
  virtual clock. This is not a live smoke run.
- Eight opt-in Chromium tests were attempted. All failed at browser launch with
  `TargetClosedError` / Chromium abort before test behavior ran. This environment
  therefore did not verify browser event instrumentation, the two new real-DOM
  regressions, or live Microsoft authentication. No live account was accessed.
- Forward application matched all proposed bytes and Git executable bits;
  actual reversal restored the baseline. Whitespace, publication-guard, Python
  compilation, four credential JavaScript syntax checks, shell syntax, and
  applied-copy offline tests passed. The actual checkout passed a read-only
  apply check and remained clean with all tracked bytes unchanged.

## Apply and retest on Mac and Ubuntu

Stay on the current M1 fix branch for this correction; a new branch is optional.
Copy the downloaded patch to the path below, then inspect before applying:

```sh
cd "$HOME/Downloads/otter"  # Ubuntu: use your existing checkout path
git branch --show-current
git status --short --branch
git log -1 --oneline
patch_file="$HOME/Downloads/OTTER_M1_Detached_Submit.patch"
git apply --stat "$patch_file"
git apply --check "$patch_file"
git apply "$patch_file"
git diff --check
git diff --stat
```

Use the same 3.12.14 interpreter used by your acceptance runner. This example
assumes it is in `.venv`; change the variable if yours is elsewhere:

```sh
m1_python="$PWD/.venv/bin/python"
"$m1_python" --version
PYTHONPATH=src "$m1_python" -m unittest discover -s tests -v
OTTER_BROWSER_TESTS=1 PYTHONPATH=src "$m1_python" -m unittest discover \
  -s tests -p test_auth_browser.py -v
```

The browser suite fulfills all requests locally and uses synthetic credentials.
Its two new regressions use a real detached Chromium element handle and real
capture events; one also observes a locally fulfilled form POST. The fixture
navigation tests use synthetic click transitions, not Microsoft's JavaScript.

In PyCharm, select the same 3.12.14 interpreter, set the repository as working
directory and `PYTHONPATH=src`, then run unittest discovery in `tests`. Set
`OTTER_BROWSER_TESTS=1` only for the separate browser fixture configuration.
Run the live smoke as the module `provadis_m1.acceptance` with the arguments
below; keep the credential file outside the project and out of shared run
configuration files.

Run the live smoke after those checks, once on each host, with the existing
private credential file. Never `source` it or pass password values on the CLI:

```sh
credentials_file="$HOME/.config/otter/credentials.env"
PYTHONPATH=src "$m1_python" -m provadis_m1.acceptance smoke \
  --credentials-file "$credentials_file" --decline-passkey
```

Each run creates new isolated acceptance profiles and a private report under
`.m1-acceptance/run-...`. A passed default smoke contains seven passed steps,
validated API samples in every step, password POST evidence in both fresh
phases, and no credential attempts in strict wet probes. Inspect the generated
schema-2 report; do not use an older report to assess this fix. If needed, follow
with `--fresh-cycles 2` for additional independent fresh profiles.

For failure analysis, substitute the exact run/step names printed in the report:

```sh
run_dir="$PWD/.m1-acceptance/run-REPLACE_WITH_ACTUAL_NAME"
cat "$run_dir/report.json"
find "$run_dir" -name 'navigation-*.jsonl' -print
find "$run_dir" -name 'failure-*.json' -print
```

Inspect only the selected navigation JSONL and sanitized failure report. The
profiles themselves contain session secrets and must never be uploaded. Review
attempt counters alongside observations/outcomes and reacquisition events. An
uncertain outcome that remains on password until timeout is an intentional
bounded failure, not permission to loop password submission.

After short smoke passes on both hosts, rerun the active/idle matrix in
`m1-acceptance.md` on the final source. The owner's earlier successful 24-hour
test is encouraging but does not verify this changed code or natural-expiry
automatic password recovery. M1 remains open until unattended recovery is
demonstrated without operator input. MFA/CAPTCHA/tenant policy remains a possible
blocker and cannot be solved by silently bypassing challenges.

To reverse this patch before making further overlapping edits:

```sh
git apply --reverse --check "$patch_file"
git apply --reverse "$patch_file"
```

After reviewing and testing, use explicit staging paths (no profiles/logs):

```sh
git add README.md docs/m1-acceptance.md docs/m1-detached-submit.md \
  src/provadis_m1/credential_step.py src/provadis_m1/auth_flow.py \
  src/provadis_m1/browser.py src/provadis_m1/acceptance.py \
  src/provadis_m1/__main__.py src/provadis_m1/navigation_log.py \
  tests/auth_support.py tests/test_credential_step.py tests/test_auth_browser.py \
  tests/test_auth_flow.py tests/test_auth_runner.py tests/test_acceptance.py \
  tests/test_cli.py tests/fixtures/auth/DryLogin-4_SignIn-EMail.html \
  tests/fixtures/auth/DryLogin-7_SignIn-EnterPassword.html
git diff --cached --check
git diff --cached
git commit -m "fix(auth): recover detached credential controls safely" \
  -m "Refs #ISSUE_NUMBER"
```

Replace `ISSUE_NUMBER` with the existing authentication issue number. `Refs`
links the commit without prematurely closing the issue. Keep the M1 PR open
until the corrected smoke and remaining live acceptance gates pass on the final
source. No Git commit or remote change is performed by the patch.
