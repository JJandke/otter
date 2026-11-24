# M1 rebuild verification — 24 November 2026

Source baseline: `8d4c39c9de3d5c2b05f6cb83d950050c28f7b9d0`, clean
`fix/m1-session-diagnostics`. Work was performed on isolated copies; the original
checkout was left unchanged. No commits, pushes or merges were made.

## Executed checks

| Check | Result | What it establishes |
| --- | --- | --- |
| Evidence inventory and full ZIP member reads/CRC | 156 entries, 138 files, 48 distinct contents; no CRC failure | Supplied archive is readable; both descriptions, 15 HTML documents and 5 screenshots inspected |
| Unit discovery with available Python 3.14.7 | 115 tests discovered: 110 passed, 5 browser tests skipped | HTML-backed/mock navigation, retry/expiry boundaries, API validation, CLI, state lock, credentials, logging and safe projection contracts |
| Explicit Chromium fixture suite | 5 errors, each at browser launch before a page opened | Environment cannot execute these browser tests; no locator/rendering/persistence result established |
| Existing sandboxed Chromium smoke test | Failed (`browser_smoke_failed`) | Browser startup unavailable in this execution |
| Python 3.12 interpreter discovery, offline | No interpreter found | Declared Python 3.12.14 runtime not tested here |
| Python compileall and shell syntax | Passed | Source compiles on available runtime; shell scripts parse |
| Forward patch application to untouched source copy | Passed | Patch applies cleanly to the actual inspected tree |
| Applied copy compared with proposed source | Exact content and Git executable-mode match | Patch contains the complete proposed source/test/docs changes |
| Tests on applied copy | Same 110 passes and 5 skips | Patch reproduces the tested candidate |
| Reverse patch check/application | Passed; baseline content/Git modes restored exactly | Reversal works before overlapping edits |
| Public-tree guard in isolated temporary Git index | Passed | Staged proposed files pass the existing private-artifact/pattern guard |
| Original checkout recheck | Same HEAD/branch and clean tree | User checkout preserved |

The browser tests were explicitly attempted with `OTTER_BROWSER_TESTS=1`, not
merely skipped. Playwright reported `TargetClosedError` from
`launch_persistent_context`; Chromium exited immediately in the restricted host.
The cause below that startup error was not established. No sandbox settings were
weakened, personal profile used, or dependency/runtime replaced to work around it.

## Commands executed

In the isolated implementation/applied source copy, using the available interpreter:

```sh
PYTHONPATH=src /Users/Johannes/Downloads/otter/.venv/bin/python \
  -m unittest discover -s tests -v
/Users/Johannes/Downloads/otter/.venv/bin/python -m compileall -q src tests scripts
sh -n scripts/m1 scripts/setup .githooks/pre-commit
OTTER_BROWSER_TESTS=1 PYTHONPATH=src \
  /Users/Johannes/Downloads/otter/.venv/bin/python \
  -m unittest discover -s tests -p test_auth_browser.py -v
UV_CACHE_DIR=/tmp/otter-uv-cache uv python find 3.12 --offline
```

Patch validation used `git apply --check`, forward `git apply`, complete tree
comparison, `git apply --reverse --check`, reverse application and a second
complete tree comparison. The original repository was only read.

## Test interpretation

`test_auth_flow.py` uses sanitized captured controls with a small HTML/selector
adapter. It verifies dry/wet branching, off-screen-field exclusion, dashboard
heading selection, passkey-state recognition, password choice, resumed password/
KMSI, rejected credentials, unknown/missing/duplicate controls, state timeouts,
origin races, form replacement and bounded actions. It is not Playwright rendering.

`test_auth_runner.py` uses that HTML adapter with mocked Playwright lifecycle,
response events and direct requests. It verifies valid dry/wet/already-authenticated
results, stale-token renewal without restart, 401 recovery, repeated 401, no 403
login loop, interactive fallback, API rejection, global timeout, retained failures
and explicit debug-window waiting. Native Cancel and OAuth are not emulated.

The retained boundary tests validate exact URLs/methods, no redirects, body limits,
status/content/schema/count/date failures, optional message fields, synthetic
secrets excluded from diagnostics, credentials-file rules and profile locking.
The failure tests ensure original page content/pixels are never copied and only
fixed labels/numeric control geometry enter the projection document.

The five `test_auth_browser.py` cases are opt-in real Chromium tests with all
requests locally routed and synthetic click transitions. They exercise real
locators for all 15 fixtures, dry/wet persistent-profile restart, a mocked API
response, the passkey-rejection wrapper, DOM waits and projection screenshots.
They do not execute Microsoft's real JavaScript or real account authentication.
CI now runs them after installing Chromium; CI itself was not run in this task.

## Acceptance still required

- All five Chromium fixture tests on an unrestricted host.
- Offline and browser tests with the declared Python 3.12.14 locked environment.
- Real fresh login, including native Cancel and optional method/KMSI prompts.
- Separate live acceptance of opt-in passkey rejection, if that mode is desired.
- Real persistent-profile fetch and two-browser verify.
- Natural expiry/reauthentication and a defined idle-session interval.
- Current tenant MFA/account/consent behavior when those prompts occur.
- Actual failure artifacts reviewed locally for usefulness and privacy.

No live Microsoft credentials, cookies, tokens or timetable responses were used
by this execution. No fully verified live reliability claim is made. Earlier
repository feasibility notes are historical evidence for earlier versions.
