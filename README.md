# OTTER — Organised Timetable Translation and Event Retrieval

M1 provides legitimate browser authentication, persistent-session reuse and
validation of the exact Provadis timetable API. M2 adds normalized observations,
conservative snapshot assessment and recoverable SQLite persistence. Event
synchronization, ICS generation, scheduling and deployment remain later milestones.

**Status — 26 November 2026:** the owner reports increments A–F (Issues #4–#9)
passed their tests on macOS and Ubuntu. Issue #10 adds the final composed tests
and [M2 acceptance/operator guide](docs/m2-acceptance.md); verification of this
increment on both hosts is still required. M1's long-duration authentication
acceptance remains a separate gate; it is not inferred from storage tests.

## M2 operators start here

Read [current commands, acceptance matrix and evidence](docs/m2-acceptance.md).
Schema is version **3**. Existing stores require explicit migration if older;
never initialize over missing/corrupt history. Backup/restore and explicit
quarantine expiry are in the [recovery runbook](docs/m2-recovery.md).

```sh
./scripts/otter status --directory .otter-data
./scripts/otter check --directory .otter-data
```

These commands inspect an existing private store without authentication. Use
init only for a genuinely new store. M2 fetch uses its own
.otter-runtime/.m1-state profile, separate from .otter-data and M1 acceptance.
No SQL server, ORM, Docker, scheduler or production service is installed.
The M1 commands below keep their existing behavior and do not write M2 observations.

**Normal operation must not require manual reauthentication.** Supply a private
credential file to authorize automatic password-based login when needed. If the
live tenant requires an interactive MFA/CAPTCHA/other unsupported challenge,
unattended operation remains unproven and M1 cannot be accepted under this policy.

Read [the current M1 runner, commands and complete acceptance checklist](docs/m1-acceptance.md).
Earlier [authentication investigation](docs/m1-authentication-rebuild.md) and
[SSO-return diagnosis](docs/m1-sso-return-race.md) explain the navigation evidence.

## Application commands

```sh
./scripts/m1 fetch --credentials-file "$HOME/.config/otter/credentials.env" \
  --decline-passkey --navigation-log
./scripts/m1 verify --credentials-file "$HOME/.config/otter/credentials.env" \
  --decline-passkey --navigation-log
```

Healthy sessions skip credential forms. Required login forms use the configured
file automatically. `login` can also start an explicit assisted or interactive
session. No `.env` is searched for or sourced implicitly; the owner-only file must
be outside the checkout and contain `OTTER_USERNAME` and `OTTER_PASSWORD`.
Passwords are never passed as command-line values or saved in reports.

Omit `--credentials-file` for strict session-reuse tests. `verify` always uses two
headless browser lifetimes with at least two validated direct requests each. Its
`restart_reuse_demonstrated` is false if credentials were attempted.
Credential attempts are reported separately as `credential_attempted`; matching
form POST observations set `credential_submission_used`. These are distinct from
API validation. See [detached-submit recovery and retesting](docs/m1-detached-submit.md).

The opt-in `--decline-passkey` behavior rejects WebAuthn requests on specifically
allowlisted Microsoft pages, supplying no authenticator or MFA response. It
requires an explicit credential file. Unknown screens and failed authentication
remain bounded, nonzero failures. HTTP 403 does not initiate a login loop.

## Finite live acceptance tests

```sh
./scripts/m1-acceptance smoke --credentials-file "$HOME/.config/otter/credentials.env" \
  --decline-passkey
./scripts/m1-acceptance active --credentials-file "$HOME/.config/otter/credentials.env" \
  --decline-passkey --duration-hours 26 --interval-seconds 3600
./scripts/m1-acceptance idle --credentials-file "$HOME/.config/otter/credentials.env" \
  --decline-passkey --idle-hours 25
```

These are real sign-ins/API calls when explicitly started. Each test creates new
private profiles and preserves existing ones. Reports are under `.m1-acceptance/`,
excluded from Git and protected by the publication guard. The runner stops on the
first unexpected failure. It uses the chosen Python interpreter for each fresh
application process, has finite child deadlines, locks waiting profiles, records
actual timing and distinguishes reauthentication from cookie-only reuse.

The shell wrappers use `.venv/bin/python`. To preserve the current Mac's 3.14
longevity environment while testing the declared 3.12 target, use the separate
3.12 interpreter with `PYTHONPATH=src python -m provadis_m1.acceptance ...` as shown
in the runbook. Do not mix runtimes within a measured series.

## Offline checks and browser fixtures

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
OTTER_BROWSER_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover \
  -s tests -p test_auth_browser.py -v
```

Normal discovery includes synthetic runner/clock/subprocess tests and skips eight
opt-in Chromium fixtures. The explicit browser suite routes every request locally
and simulates transitions; it does not execute Microsoft's real login JavaScript.
On a fresh checkout, `./scripts/setup` installs Python 3.12.14, the locked stack
and Chromium and enables the publication hook. Preserve an ongoing test environment.

## State and diagnostics

Use a dedicated `.m1-state` or `.provadis-m1` directory consistently. It holds an
exclusive profile lock, including during debug inspection. Every successful
application command validates the exact API response; reaching a page is insufficient.
The portal obtains authorization; OTTER holds the exact GET's observed successful
Bearer header in memory and replays that GET without redirects or token-cache access.

`--navigation-log` writes allowlisted JSONL events. Failures create private state
reports and, when available, PNGs of sanitized control-layout projections. Original
page pixels, QR codes, input values, raw messages and timetable data are omitted.
`--keep-open-on-error` retains a failed visible browser for local inspection.

Application exits: 0 validated snapshot; 2 authentication; 3 network;
4 invalid response; 5 inconclusive empty timetable; 6 configuration;
7 upstream service; 8 navigation/bootstrap; 130 interrupted.
Runner exits are separately documented in the acceptance runbook.
