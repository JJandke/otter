# M1 authentication rebuild — investigation and owner runbook

## A. Investigation

### Source baseline

Read-only checkout: `/Users/Johannes/Downloads/otter`, clean working tree, branch
`fix/m1-session-diagnostics`, commit
`8d4c39c9de3d5c2b05f6cb83d950050c28f7b9d0`.
It was five commits ahead of cached `origin/fix/m1-session-diagnostics`.
Local/cached remote `main` were `01b4b80`; cached `origin/master` was `b5e7f52`.
No remote fetch was performed, so these are local observations, not a current
GitHub inventory. No original files, commits, branches or profile state changed.
Tracked files were copied to isolated baseline and implementation directories.

`scripts/m1` starts `provadis_m1.__main__`; login/fetch/verify call `browser_run`.
The rebuild retains the CLI, credential loader, private-state ownership checks and
exclusive `flock`, browser settings, exact-API capture/direct GET, validation and
sanitized JSONL infrastructure. `AuthFlow` replaces `StudentNavigation` and
`PasswordLogin`; tests tied to the obsolete flags are replaced by state tests.

### Primary evidence

`Steps.zip` SHA-256:
`d777b34cd6b5569d216a1c97ec4b98ebdeda27254961351e850c42dc31a542b6`.
All 156 ZIP entries were inventoried and all 138 file members were read and
CRC-checked (48 distinct file contents, about 47.7 MB uncompressed). Both click
descriptions, all 15 top-level HTML captures and all five screenshots were
examined. Repeated JS/CSS/image/font support assets are not redistributed.

`tests/fixtures/auth/` contains minimal extracted controls from those 15 HTML
pages, with source filenames/hashes in `provenance.json`. Scripts, hidden values,
identity, personal events, session URLs and screenshot pixels are omitted. A small
CSS rule reproduces the captured off-screen prefill behavior. These selector
fixtures do not reproduce Microsoft's application or its dynamic scripts.

| Finding | Evidence | Difference from the old implementation |
| --- | --- | --- |
| Password page contains off-screen email | Dry 7: `input[name=loginfmt].moveOffScreen`, real password `#i0118` | Old code used visible name matches and history flags. An off-screen element can still satisfy Playwright visibility, blocking direct/resumed password entry. |
| Email page contains off-screen password | Dry 4: real email `#i0116`, prefill `#i0118.moveOffScreen` | Old code inferred stage from both fields and earlier submissions rather than distinguishing actual fields. |
| Wet login skips B2C/Microsoft | Wet 1–5 go through student/login buttons straight to dashboard | Old root-dashboard handling required `student_sso_clicked`; wet login never sets it. |
| Duplicate study labels | Dry 9/Wet 3 have sidebar text plus dashboard `h2.header` | Exact `get_by_text` could match multiple visible nodes. The rebuild selects the heading. |
| Method picker can be resumed directly | Dry 6: role button `Use my password`, `data-test-cred-id=1` | Old selection required a previous email submission by the tool. |
| KMSI can be resumed directly | Dry 8: `#KmsiDescription`, `#KmsiCheckboxField`, `#idSIButton9` | Old code required its own prior password submission and omitted Don't show this again. |
| B2C buttons are aliases | Dry 3: recorded Microsoft button and student button both click `AzureADExchange` | Earlier claims that these necessarily choose different providers are contradicted by their onclick handlers. `LiveIDExchange` is the separate private Microsoft provider. |
| Passkey Cancel is native UI | Screenshot 5, Microsoft `/common/bridge/fido` | There is no HTML Cancel selector. The retained opt-in rejection script simulates refusal; it does not perform a verified native UI click. |

These are reproducible code/evidence mismatches. This execution did not obtain a
live trace proving which mismatch caused every previous failure. The captures
cannot establish tenant policy, redirect timing or live renewal behavior.

## B. Navigation model

| State | Evidence-based recognition/action | Conditional continuation |
| --- | --- | --- |
| Public portal | Heading `Für Studierende` | Click → student login |
| Student login | Button `Jetzt anmelden` | Click → wet dashboard or B2C/redirect |
| B2C | Recorded `mit meinem Microsoft-Konto anmelden`; student alias if missing | Click → Microsoft/redirect |
| Email | `#i0116[name=loginfmt]:not(.moveOffScreen)` | Explicit credentials or operator → passkey/picker/password |
| Native passkey bridge | Exact allowlisted origin/path | Operator Cancel or opt-in rejection → redirect/picker |
| Method picker | Role button `Use my password` | Click → password |
| Password | `#i0118[name=passwd][type=password]:not(.moveOffScreen)` | Explicit credentials or operator → KMSI/redirect/additional prompt |
| Stay signed in | `#KmsiDescription` | Check `#KmsiCheckboxField` if present; submit `#idSIButton9` |
| Dashboard | Heading `Mein Studium` | Click → study page |
| Study | Heading `Stundenplan` | Click → timetable |
| Timetable | Visible `.p-panel-title` containing `Stundenplan` | Wait for exact API GET; page alone is not success |
| API candidate | Browser's exact HTTP-200 GET provides an in-memory Bearer candidate | Replay fixed direct GET and validate |
| API 401 | At most one portal-home restart | Same detector → authentication or clear failure |
| API 403 | Do not restart login automatically | Access-denied failure |
| Unknown trusted page | Visible mode allows operator interaction; headless has bounded waits | Known state or diagnostic timeout |
| Unexpected origin | Reject before actions/credentials | Authentication failure |

Captured dry login traverses the portal, B2C, email, native Cancel, method picker,
password, KMSI and authenticated portal pages. Captured wet login traverses just
student/login buttons, dashboard, study and timetable. An already authenticated
API/timetable can skip all clicks. Resumed sessions may begin at password, choice
or KMSI without prior tool submissions. All scenarios use the same detector.

Account selection, MFA, consent and password changes are not demonstrated.
No selectors for those prompts are invented. Complete legitimate extra prompts
manually in a visible browser on trusted hosts; headless sessions fail with state
diagnostics. New hosts require review before sending credentials.

## C. Implementation

- `auth_flow.py` detects current controls, checks origins, bounds actions, and waits
  for document/DOM changes. Email/password submit once per run. Other navigation
  states can act at most twice across separate visits; entry controls now act only
  once per attempt. See [the SSO-return follow-up](m1-sso-return-race.md).
- `browser.py` shares persistent-session handling and allows one 401 recovery.
  It observes optional popups, closes surplus restored tabs before starting, and
  validates the fixed direct API GET. It never reads browser token stores.
- `failure_diagnostics.py` records sanitized state/control counts and a PNG of
  fixed public labels at measured control positions in a new inert document.
  It never copies original pixels, HTML, input values, QR codes or event details.
- JSONL records allowlisted states, attempted/completed actions and navigation,
  without headers, upstream messages, form values or raw console/errors.
- API validation keeps strict HTTP/JSON/schema/count/date checks. `message` and
  `message2` are optional, matching the requested contract; their types are checked
  when present. Existing empty-timetable and offset-aware-date policies remain.

Native Cancel has no page locator. Normal visible login leaves it to the operator.
The optional `--decline-passkey` script rejects WebAuthn public-key requests with
`NotAllowedError` on specific Microsoft origins, preserving other credential
requests. It supplies no authenticator, OAuth response or MFA answer. Its
compatibility with the current tenant must be tested live. No CAPTCHA bypass,
OAuth construction, refresh-token replay, SQLite, ICS or deployment is included.

The exclusive profile lock covers browser launch, authentication, API reads,
debug inspection and close. Chromium also locks its profile. Use dedicated state,
not your personal browser profile. Authentication has a global bootstrap budget
and a headless unchanged-state budget. One 401 recovery receives one fresh
bootstrap budget without resetting action/credential limits. DOM waits have a
bounded heartbeat to pump network events; no fixed authentication sleeps or
`networkidle` assumptions are used. Only requested sampling/debug pauses wait a
fixed interval. HTTP 403 is not assumed to mean expiry.

## D. Verification

See `docs/m1-rebuild-verification.md` for exact executed commands/results.
The normal suite uses sanitized HTML with a small selector adapter and mocked
Playwright navigation/API responses. It tests branching, error and retry budgets,
API parsing, credential restrictions and diagnostic contracts, not browser rendering.

Six opt-in tests use actual Chromium with every network request routed locally.
They test real locators on 15 fixtures, synthetic dry/wet transitions, persistent
cookie restart, the passkey rejection wrapper, DOM waits and safe projections.
They do not execute Microsoft's JavaScript or prove native Cancel behavior.

Python 3.14.7 was available; the declared 3.12.14 interpreter was unavailable.
No user environment or browser installation was replaced. Chromium exited before
opening a page in this restricted execution, so actual browser and live tests
remain unverified. Earlier live feasibility notes belong to earlier commits.

## E. Commands for the owner

### Inspect and apply

Save the attachment as `~/Downloads/OTTER_M1_Authentication_Rebuild.patch`.
Review each command's output before continuing:

```sh
cd /Users/Johannes/Downloads/otter
git status --short
git branch --show-current
git branch -avv
git rev-parse HEAD
git log --oneline --decorate -8

patch_file="$HOME/Downloads/OTTER_M1_Authentication_Rebuild.patch"
git apply --stat "$patch_file"
less "$patch_file"
git apply --check "$patch_file"
git apply "$patch_file"
git diff --check
git diff --stat
git status --short
```

Expected branch: `fix/m1-session-diagnostics`. Preserve this M1 branch; do not
apply to `main` or switch away from unrelated work blindly. `--check` changes
nothing; `git apply` edits files without creating a commit. New files appear as
untracked until staged, so plain `git diff` does not show their contents.
If the baseline has changed or overlapping edits exist, reconcile first. Do not
use `--reject` to partially install authentication changes.

### Automated tests

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests scripts
sh -n scripts/m1 scripts/setup .githooks/pre-commit
OTTER_BROWSER_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover \
  -s tests -p test_auth_browser.py -v
```

The first command should pass with exactly six browser tests skipped. The last
must pass all six on an unrestricted host; it makes no live authentication calls.
If Chromium is absent, install it with `.venv/bin/python -m playwright install chromium`.

Check the declared runtime in a separate environment, preserving the existing
long-running session experiment:

```sh
uv venv --python 3.12.14 /tmp/otter-m1-python312
UV_PROJECT_ENVIRONMENT=/tmp/otter-m1-python312 uv sync --locked \
  --python /tmp/otter-m1-python312/bin/python
PYTHONPATH=src /tmp/otter-m1-python312/bin/python -m unittest discover -s tests -v
OTTER_BROWSER_TESTS=1 PYTHONPATH=src /tmp/otter-m1-python312/bin/python \
  -m unittest discover -s tests -p test_auth_browser.py -v
```

This may download Python/dependencies. In PyCharm select the intended interpreter,
mark `src` as Sources Root, and run unittest discovery from `tests`. Only the
browser test configuration needs `OTTER_BROWSER_TESTS=1`. On Ubuntu use the same
commands; missing browser system dependencies may require
`python -m playwright install --with-deps chromium` in a test environment.
Visible login needs a desktop/forwarded display. No production changes are needed.

### Real dry login

Use a new state directory instead of deleting your current profile:

```sh
OTTER_TEST_ROOT="$(mktemp -d -t otter-m1)"
otter_test_state="$OTTER_TEST_ROOT/.m1-state"
./scripts/m1 login --state-dir "$otter_test_state" --headed \
  --navigation-log --keep-open-on-error
```

Keep this Terminal open for the wet test, retaining `otter_test_state`. Enter
credentials in the dedicated browser, cancel native passkey UI if presented,
and complete legitimate extra prompts. OTTER handles captured page controls.
Success requires `ok: true` and validated `event_counts` in the final JSON;
a timetable page alone does not pass. A structurally valid empty timetable remains
inconclusive (exit 5) under the existing M1 policy.

Optional credential-assisted test, using an existing owner-only file outside the
repository containing `OTTER_USERNAME` and `OTTER_PASSWORD` only:

```sh
./scripts/m1 login --state-dir "$otter_test_state" --headed \
  --credentials-file "$HOME/.config/otter/credentials.env" \
  --decline-passkey --navigation-log --keep-open-on-error
```

Do not source the file or put secrets in command arguments. Use a different new
`mktemp` state for a fresh assisted test; reusing successful state tests wet login.
After visible testing succeeds, `--headless` may replace `--headed`; remove
`--keep-open-on-error`, since no window exists. Extra MFA/account/consent prompts
may still require a visible browser and cannot be completed by this automation.

### Real wet login

Use the same state and separate browser lifetimes:

```sh
./scripts/m1 fetch --state-dir "$otter_test_state" --navigation-log
./scripts/m1 verify --state-dir "$otter_test_state" --navigation-log
```

`fetch` must validate without unnecessary credential actions. `verify` must have
two browser runs, each with at least two validated direct samples. This proves
short-term restart reuse, not long-term renewal. Repeat after a documented idle
interval relevant to your use case. Record host, Python/browser versions, result
category, counts and sanitized action/state evidence; never personal event data.

An expired headless session may exit 2 with `session_requires_interactive_login`.
The detector enters the login workflow but never implicitly reads credentials.
Resume that same profile interactively, then retest:

```sh
./scripts/m1 login --state-dir "$otter_test_state" --headed \
  --navigation-log --keep-open-on-error
./scripts/m1 fetch --state-dir "$otter_test_state" --navigation-log
```

Do not invalidate your primary account's sessions just to test. Expiry, repeated
401 and 403 are covered with mocks; confirm natural expiry or revocation when
an appropriate real test session is available.

### Failure diagnostics

The JSON summary reports the exact JSONL and `failure_artifacts` filenames.
Substitute them in these examples:

```sh
less "$otter_test_state/navigation-REPORTED-FILENAME.jsonl"
.venv/bin/python -m json.tool "$otter_test_state/failure-REPORTED-ID.json"
open "$otter_test_state/failure-REPORTED-ID.png"
```

`auth_state` names the last detected state; `auth_actions` counts attempts.
JSONL distinguishes attempted/completed actions. `last_direct_api_status` separates
API rejection from a UI stall. A missing API request does not itself prove expiry.
Control counts of 0 indicate missing/changed controls; more than 1 is ambiguous.
The PNG intentionally cannot show upstream messages, account identity or native
browser dialogs. Inspect those locally using a visible reproduction.

`--keep-open-on-error` waits until window close, then returns the original error.
Ctrl+C exits 130. The older `--failure-pause-seconds 60` is a bounded alternative.
Do not run a second process until the first releases its profile lock.

### Reverse the uncommitted patch

```sh
git apply --reverse --check "$patch_file"
git apply --reverse "$patch_file"
git status --short
```

This preserves profiles and reverses only patch changes. If overlapping edits
make the check fail, reconcile them first. Do not use `git reset --hard` or
`git clean`. If staged, unstage only reviewed patch paths with
`git restore --staged -- <paths>` before reversing. If already committed,
prefer `git revert <commit-sha>` after checking the tree; it creates a visible
undo commit without rewriting shared history.

### Commit and link an Issue

Reuse your existing debugging Issue if it matches this scope and assign it to
**M1 — Foundation and Authentication**. Replace example `123` below with the real
number. Review explicit paths before staging:

```sh
git add -- .github/workflows/tests.yml README.md docs/m1-authentication-rebuild.md docs/m1-rebuild-verification.md \
  src/provadis_m1/auth_flow.py src/provadis_m1/failure_diagnostics.py \
  src/provadis_m1/browser.py src/provadis_m1/__main__.py \
  src/provadis_m1/navigation_log.py src/provadis_m1/validation.py \
  src/provadis_m1/navigation.py src/provadis_m1/password_login.py \
  tests/auth_support.py tests/test_auth_flow.py tests/test_auth_runner.py \
  tests/test_auth_browser.py tests/test_failure_diagnostics.py tests/fixtures/auth \
  tests/test_browser.py tests/test_cli.py tests/test_navigation_log.py \
  tests/test_validation.py tests/test_navigation.py tests/test_password_login.py
git diff --cached --check
git diff --cached --stat
git diff --cached
.venv/bin/python scripts/check_public_tree.py
git commit -m "fix(auth): rebuild M1 navigation from captured page states" \
  -m "Refs #123"
```

`git add` stages only reviewed patch files, including deletions. The public-tree
check scans staged bytes for private artifacts; manual review is still necessary.
`fix(auth)` means a bug fix scoped to authentication. `Refs #123` links progress
without claiming acceptance. Once acceptance passes, use `Closes #123` in the PR
body. GitHub normally closes that Issue when the PR reaches the default branch.
A commit on the milestone branch alone does not complete the milestone.

No push is required to apply, test or commit locally. When you choose to publish:

```sh
git fetch origin
git log --oneline --left-right HEAD...origin/fix/m1-session-diagnostics
git push origin fix/m1-session-diagnostics
```

Fetch refreshes cached refs; inspect divergence before pushing. Your local branch
was already five commits ahead before the patch, so pushing publishes those too.
Do not force-push or merge unrelated remote changes without review.

Create a **draft PR from `fix/m1-session-diagnostics` to `main`** after reviewing the
patch and offline tests. Keep it draft until unrestricted Chromium tests, live
dry/wet/expired-session acceptance and declared-runtime checks pass. Merge when
M1 Issues are resolved or explicitly accepted, diagnostics are private, and CI
is green. Start later milestones on separate branches from updated `main`.

Suggested PR title: `fix(auth): rebuild dry and persistent M1 authentication`.
Describe the off-screen-input/dashboard problem, resulting state-based behavior,
validation run and remaining limitations. Attach sanitized results, not raw
captures, HARs, cookies, profiles, credentials or event data.

### Recommended Issues

1. **M1: Rebuild dry and persistent Microsoft authentication from captured states**
   - Description: replace historical gates blocking resumed password/dashboard
     handling; use captured controls and validate the exact timetable API.
   - Acceptance: fresh and wet login complete; wet login skips unnecessary prompts;
     direct password/chooser/KMSI entry works independently of earlier submissions;
     API failures never pass; offline and browser fixture tests pass; host/runtime
     and sanitized real dry/wet results are recorded.
2. **M1: Verify native passkey cancellation and expired-session recovery**
   - Description: compare manual Cancel and opt-in WebAuthn rejection on the current
     tenant; exercise a naturally expired persistent test session.
   - Acceptance: refusal returns to a supported state; at most one 401 recovery;
     repeated 401/403/rejected credentials never loop; visible fallback supports
     legitimate extra prompts; results come from live tests, not only HTML.
3. **M1: Establish Python 3.12 and idle-session acceptance with private diagnostics**
   - Description: resolve the existing Python 3.14/runtime mismatch and measure
     restart/idle reuse on the declared locked stack.
   - Acceptance: Python 3.12.14 unit/browser tests and two-browser verify pass;
     a documented idle interval yields valid data or clear reauthentication;
     failure artifacts contain no session secrets; concurrent profile access is
     rejected. Define the interval before claiming longevity. No deployment needed.

Possible follow-up commits: `test(auth): cover accepted login regressions` and
`docs(auth): record M1 live session acceptance`, each with `Refs #<real-issue>`.

## F. Remaining limitations

Live Microsoft JavaScript, tenant redirects, native Cancel, MFA, consent and
account selection cannot be proven by static captures. The screenshots used
Brave; the project uses Playwright Chromium. Native dialogs can differ.
Passkey rejection is opt-in and must be retested after browser/Microsoft changes.

Persistent cookies do not guarantee session validity. The portal performs silent
renewal; OTTER restarts normal navigation once on API 401 and cannot defeat tenant
sign-in policy. Exact origin/label checks intentionally fail for unknown hosts,
translations or changed markup; update from fresh sanitized evidence.

Existing validation requires offset-aware dates, end after start, an 8 MiB body
limit and a nonempty snapshot. New API formats or a legitimate empty timetable
can therefore fail conservatively. Optional message fields are now truly optional.
A safe control-layout image cannot show upstream error text or browser chrome.
Earlier live results belong to earlier source versions; this rebuild needs the
acceptance steps above before being called verified.
