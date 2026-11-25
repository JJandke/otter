# OTTER — Organised Timetable Translation and Event Retrieval

M1: legitimate browser authentication and timetable API validation. No database,
ICS generation, production service or deployment is implemented.

The authentication rebuild uses the owner's 24 November 2026 dry/wet captures.
Offline unit tests pass; actual Chromium fixture tests and live Microsoft login
still require acceptance on an unrestricted host. Earlier feasibility results
apply to earlier source versions, not this rebuild.

Read [the investigation and runbook](docs/m1-authentication-rebuild.md) for the
navigation model, testing, diagnostics, rollback, GitHub Issues and merge criteria.
It supersedes earlier navigation/password-stage documents in `docs/`, retained
as historical investigation notes. See [the verification record](docs/m1-rebuild-verification.md).

The follow-up [SSO return diagnosis and M1 completion plan](docs/m1-sso-return-race.md)
prevents repeated entry clicks during callback completion and adds sanitized UI
error categories. Entry controls act once per authentication attempt; only the
existing explicit 401 recovery resets that entry guard.

## Run

Use the existing environment without replacing it during a session-longevity
experiment. On a fresh checkout, `./scripts/setup` installs the declared Python
3.12.14 environment and locked Playwright 1.62.0/Chromium, and enables the versioned
pre-commit guard. The rebuild adds no runtime dependencies.

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
./scripts/m1 login --headed --navigation-log --keep-open-on-error
./scripts/m1 fetch --navigation-log
./scripts/m1 verify --navigation-log
```

`login` opens a dedicated persistent profile. Enter credentials/MFA in that window
and cancel the native passkey dialog if it appears. OTTER clicks the captured
portal, password-choice and stay-signed-in controls as they appear. Every success
requires actual direct API response validation. Only counts and sanitized
diagnostics reach stdout; personal events are not saved.

`fetch`/`verify` reuse that profile without implicitly loading credentials. Use
`login` or `fetch --headed` if interaction is needed. `verify` launches two separate
headless browser lifetimes with at least two validated direct samples each.

Explicit credential assistance requires an existing owner-only file outside the
repository with `OTTER_USERNAME` and `OTTER_PASSWORD`. No repository `.env` is
implicitly loaded or sourced.

```sh
./scripts/m1 login --headed --credentials-file "$HOME/.config/otter/credentials.env" \
  --decline-passkey --navigation-log --keep-open-on-error
```

`--decline-passkey` rejects WebAuthn public-key requests on allowlisted Microsoft
pages; it does not click native browser UI or supply an authenticator. Its live
behavior needs acceptance testing. Omit it to cancel the dialog manually.

## State and diagnostics

Default private state is `.m1-state`, containing a locked `profile/`. Use the same
`--state-dir` for login/fetch/verify. Its final directory name must be `.m1-state`
or `.provadis-m1`. The exclusive profile lock stays held during debug inspection.

`--navigation-log` writes allowlisted JSONL state/action/navigation events.
Failures write a private JSON report and, when possible, a PNG of a sanitized
control-layout projection. Original page pixels, HTML, input values, QR codes and
timetable data are never copied. `--keep-open-on-error` holds a visible failed
browser until window close or Ctrl+C; `--failure-pause-seconds 60` is the bounded
alternative.

The bootstrap and headless unchanged-state timeouts are configurable. One API-401
recovery may start one new bootstrap budget; credentials/action budgets survive.
Email/password submit at most once per run. HTTP 403 never starts a login loop.
Only `/student/schedule` is supported; uncaptured custom routes are rejected.

The portal obtains its own authorization. OTTER observes the exact timetable GET's
successful Bearer header in memory and replays the exact GET without redirects.
It never reads token caches, exports storage, constructs OAuth requests or replays
refresh tokens.

## Browser fixture tests

```sh
OTTER_BROWSER_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover \
  -s tests -p test_auth_browser.py -v
```

These use real Chromium with every network request routed locally, synthetic
transitions and sanitized captured controls. They do not run Microsoft's login
JavaScript. Normal unittest discovery skips these six opt-in tests.

| Exit | Category | Meaning |
| --- | --- | --- |
| 0 | validated_snapshot | Every requested direct API sample passed validation |
| 2 | authentication | Access denied, interaction required, untrusted origin or rejected credentials |
| 3 | network | Portal/API transport or response read failed |
| 4 | invalid_response | HTTP/content/schema/count/date validation failed |
| 5 | suspicious_response | Structurally valid but empty timetable; existing M1 policy is inconclusive |
| 6 | configuration | Unsafe/locked state, invalid options or unavailable dependencies/browser |
| 7 | service | HTTP 429 or 5xx |
| 8 | bootstrap | Missing/ambiguous controls, unexpected state, transition limit or timeout |
| 130 | interrupted | Operator interrupted the run |
