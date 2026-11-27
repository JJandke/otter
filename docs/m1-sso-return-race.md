# M1: SSO return race — fix and acceptance plan

## Evidence and diagnosis

Patch baseline: clean `fix/m1-session-diagnostics` at
`d5811a09a18b929d4fff4aab28c9b07ca51556f9` (25 November 2026).
Local and cached remote milestone refs matched. No remote fetch, user-repository
edit, commit, push, live authentication or profile access was performed.

The supplied failed-run summary and navigation log show:

| Elapsed | Observed |
| --- | --- |
| 2.14 s | Initial portal-login action completed |
| 3.28 s | B2C Microsoft-provider action attempted and completed |
| 7.11 s | Return to portal callback |
| 7.38 s | Portal navigated to `/login/student` after SSO |
| 7.49 s | OTTER attempted the portal-login action again |
| 8.33 s | `authentication_ui_operation_failed`, with no second action completion |

The failure JSON reports zero recognised controls, including `portal_login: 0`.
The attached control-layout PNG is therefore correctly empty: no matching controls
were available when diagnostics probed the page. It is not evidence of an image
capture malfunction or of rejected credentials. The last detected state was
`portal_login`, not necessarily the DOM state that still existed after the error.

Both successful supplied logs also pass through `/login/student` after SSO, then
leave without a second login click (about 538 ms and 1,170 ms respectively).
The captured portal login component asynchronously checks its identity/role state
and can redirect to `/` itself. A temporary login button does not establish that
another SSO attempt is required.

This strongly supports a callback/render race. The historical exception itself
was discarded, so detachment versus execution-context failure cannot be recovered.
No timetable API request occurred in the failed run: there is no HTTP 401/403
rejection to diagnose from it.

## Change

Automatic entry actions (`student`, `portal_login`, the B2C provider aliases) are
attempted once per authentication attempt, including across temporary unknown or
redirect states. Recognition still uses the current DOM; resumed password/KMSI
and authenticated pages retain their existing independent detection.

When an entry control reappears, OTTER waits for the portal-owned transition under
the existing state/global deadlines. There is no new fixed delay, reload, API
success shortcut or credential replay. If the portal never progresses, the run
still fails nonzero. `auth_action_suppressed` is logged once per suppressed state.

The driver's existing explicit HTTP-401 recovery starts one new entry sequence.
Cumulative action budgets, once-per-run credential limits, the single-recovery
limit and no-403-retry behavior survive. Dashboard/timetable recovery is unchanged.
This removes the demonstrated second-click path; it is not a claim that all
possible portal navigation races are resolved.

Future Playwright failures during state/action processing retain only fixed
categories (`timeout`, `element_detached`, `context_destroyed`, `browser_closed`,
`other`) and operation names (`detect`, `resolve_control`, `verify_document`,
`fill_control`, `check_control`, `click_control`, `wait_for_change`). Summary fields
are `auth_operation` and, after an observed UI error, `last_ui_error_kind`.
The JSONL `auth_ui_error` includes the operation/category. No exception message,
credential, DOM content, response body or session URL is recorded.

Execution-context destruction continues to permit bounded re-detection and now
recognises both common context-error forms. Arbitrary failed clicks and detached
controls are not blindly retried. Element-handle cleanup cannot replace the
original action error.

## Verification performed

| Check | Result |
| --- | --- |
| Regression on unmodified baseline source | Reproduces `authentication_ui_operation_failed` |
| Same regression on patched source | Passes; only one portal-login attempt and a validated synthetic API result |
| Full Python 3.12.14 / Playwright 1.62.0 suite | 127 discovered: 121 passed, six browser tests skipped |
| Full suite in existing Python 3.14.7 environment | Same 121 passes and six skips |
| Explicit Chromium fixture suite, Python 3.12 | All six stop at `launch_persistent_context` with `TargetClosedError`, before any page opens |
| Compileall and shell syntax | Passed |
| Forward/reverse patch checks and complete content/Git-mode comparisons | Passed |
| Public-tree guard in isolated temporary Git index | Passed |
| Read-only patch check against the actual checkout and original-file comparison | Passed; original checkout unchanged |

The Python 3.12 environment at `/tmp/otter-m1-python312` was already available and
was reused without installation or modification. Chromium's underlying startup
failure was not established. No sandbox settings were weakened. CI, real browser
navigation and live authentication were not executed successfully by this task.

The new tests use captured sanitized controls with mocked transitions/errors.
The optional additional Chromium case checks that a real visible login button
is not replayed after a simulated SSO return, and dashboard navigation can proceed.
It does not run Microsoft's real login JavaScript. The existing API-validation,
expiry, credentials, locking and privacy tests remain in the suite.

## Apply, inspect and undo

Save the delivered follow-up patch as `~/Downloads/OTTER_M1_SSO_Return_Race.patch`.
Apply it to the already rebuilt source, not to the older pre-rebuild commit:

```sh
cd /Users/Johannes/Downloads/otter
git status --short
git branch --show-current
git rev-parse HEAD
return_patch="$HOME/Downloads/OTTER_M1_SSO_Return_Race.patch"
git apply --stat "$return_patch"
less "$return_patch"
git apply --check "$return_patch"
git apply "$return_patch"
git diff --check
```

Stay on the current M1 branch. Review new/overlapping local changes before applying.
`--check` writes nothing; `git apply` creates no commit. Do not use `--reject` to
partially install authentication changes.

Before committing, an unmodified applied patch can be reversed with:

```sh
git apply --reverse --check "$return_patch"
git apply --reverse "$return_patch"
```

Overlapping edits or staging need review first. If already committed, prefer a
reviewed `git revert <commit-sha>`. Neither route should remove your browser profile.
Do not use reset-hard or git-clean as a rollback shortcut.

## Next steps to finish M1

### 1. Automated acceptance on the declared runtime

Use the separate Python 3.12 environment instead of replacing the working 3.14 venv:

```sh
m1_python=/tmp/otter-m1-python312/bin/python
"$m1_python" --version
PYTHONPATH=src "$m1_python" -m unittest discover -s tests -v
OTTER_BROWSER_TESTS=1 PYTHONPATH=src "$m1_python" -m unittest discover \
  -s tests -p test_auth_browser.py -v
```

Normal discovery should pass with six browser skips. Explicit browser discovery
must pass all six on an unrestricted host. The existing CI workflow runs these
browser tests after installing Chromium; verify a green CI run as well.
In PyCharm, select the Python 3.12 interpreter, mark `src` as Sources Root, and run
unittest discovery from `tests`. Set `OTTER_BROWSER_TESTS=1` for the browser run.

### 2. Recheck the original intermittent wet-session case

Keep the same existing test state. A new Terminal may have lost `otter_test_state`;
restore its actual directory path first. The value must not be empty:

```sh
: "${otter_test_state:?Set the existing test state directory first}"
PYTHONPATH=src "$m1_python" -m provadis_m1 fetch \
  --state-dir "$otter_test_state" --navigation-log
PYTHONPATH=src "$m1_python" -m provadis_m1 verify \
  --state-dir "$otter_test_state" --navigation-log
```

Observe several separate wet runs over normal testing, stopping on failure rather
than hammering the login service. Each success must validate the direct API.
A returning `portal_login` should now produce a suppression event instead of a
second entry click in that attempt. The return itself is not success. `verify`
requires two headless browser lifetimes with at least two direct samples each.

### 3. Establish a genuine fresh authentication baseline

Create a separate new state; preserve the existing intermittent-test profile:

```sh
fresh_m1_root="$(mktemp -d -t otter-m1)"
fresh_m1_state="$fresh_m1_root/.m1-state"
PYTHONPATH=src "$m1_python" -m provadis_m1 login \
  --state-dir "$fresh_m1_state" --headed --navigation-log --keep-open-on-error
```

Enter credentials/MFA in the dedicated browser and cancel the native passkey
prompt if presented. Alternatively, test explicit credential assistance with the
existing owner-only file outside the repository, on another fresh state:

```sh
assisted_m1_root="$(mktemp -d -t otter-m1)"
assisted_m1_state="$assisted_m1_root/.m1-state"
PYTHONPATH=src "$m1_python" -m provadis_m1 login \
  --state-dir "$assisted_m1_state" --headed \
  --credentials-file "$HOME/.config/otter/credentials.env" \
  --decline-passkey --navigation-log --keep-open-on-error
```

The previous successful credential-file command used a wet profile and had no
email/password actions, so it did not demonstrate credential filling or passkey
refusal. This fresh test must show the steps actually required by the live tenant
and a valid API result. Only then test `--headless` on a further fresh profile if
fully unattended initial login is required; remove `--keep-open-on-error` in
headless mode. Additional prompts can still require visible interaction.

Do not publish the credential file, account identifiers, raw screenshots or
session data. Success summaries, fixed state/action fields and event counts suffice.

### 4. Complete the existing longevity/idle and target-host gates

Keep the same dedicated profile, OS account, runtime and browser within a series.
Use separate runs at +1 h/+2 h and beyond 24 h (the existing matrix specifies
+23/+25/+48/+72 h). Separately leave a profile unused for at least 25 h, with no
intervening login/fetch/verify/browser use, then test a fresh headless fetch.
Record real elapsed times, exit codes, reauthentication count and diagnostics.
Do not change the existing 3.14 environment mid-series and call it 3.12 evidence.

If the target service runs on Ubuntu, repeat baseline, restart, >24 h and idle
checks in an isolated Python-3.12 test checkout/profile on that host. Follow
`docs/server-testing.md`; no Nginx/systemd changes, production deployment or edits
to `/home/ubuntu/provadis_timetable` are part of this patch. A server reboot or
account-session revocation is a separate deliberate operator decision, not an
implicit test action. The broader 7/14/30-day observations in `docs/feasibility.md`
can strengthen confidence; do not imply indefinite validity from a short pass.

### 5. Record reauthentication policy and review the M1 PR

When natural expiry occurs, confirm a clear nonzero failure or successful bounded
recovery, followed by operator login and successful verify when needed. HTTP 403
must not become a login loop. Decide whether occasional manual reauthentication
is acceptable; unattended renewal is not established just because cookies exist.

Update the existing M1 Issues with sanitized acceptance results. The current
rebuild commits reference Issues #1, #2 and #3; inspect their actual scopes before
choosing which one this regression closes. Stage only this follow-up's files:

```sh
git add -- README.md docs/m1-authentication-rebuild.md docs/m1-sso-return-race.md \
  src/provadis_m1/auth_flow.py src/provadis_m1/browser.py \
  src/provadis_m1/navigation_log.py tests/test_portal_return.py tests/test_auth_browser.py
git diff --cached --check
git diff --cached
"$m1_python" scripts/check_public_tree.py
git commit -m "fix(auth): avoid replaying login during SSO return" \
  -m "Refs #ISSUE_NUMBER"
```

Replace `ISSUE_NUMBER` with the existing relevant Issue number before committing.
The conventional `fix(auth)` title describes the behavior; `Refs` links progress.
Use `Closes #<number>` only once acceptance is satisfied. A draft PR from
`fix/m1-session-diagnostics` to `main` is appropriate now; merge when the declared
runtime/browser/CI checks and live baseline/restart/>24 h/idle/target-host gates
pass, remaining M1 Issues are resolved or explicitly accepted, and the authentication
limitations are recorded. Pushes and merges remain owner actions. Later calendar,
SQLite, ICS and deployment work belongs to subsequent milestones.
