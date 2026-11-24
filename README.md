For visible credential tests, passkey refusal, current diagnostics and fresh-profile
reset instructions, see [login-form-tests.md](docs/login-form-tests.md).

# OTTER — Organised Timetable Translation and Event Retrieval

Milestone M1: project foundation and authentication feasibility.

An isolated Python prototype for **authentication feasibility**, with no calendar,
database, public endpoint, or production job. It leaves the old application and
existing Nginx/systemd configuration alone.

**Status (24 November 2026):** the owner demonstrated a successful headless
fetch and one verification command with two separately launched headless browsers,
each validating two direct samples of 80 events. Both browsers exercised the
bounded dashboard retry. Long-duration renewal, idle recovery and target-server behavior remain open.
The owner's current working venv is Python **3.14.7**; these live results do not
establish compatibility with the declared Python **3.12.14** target. See
[the acceptance evidence](docs/feasibility.md) and [environment notes](docs/environment.md).
M1 is not yet fully accepted.

## Local setup (macOS / PyCharm)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git.
For a fresh checkout, from this project directory:

**Existing longevity-test checkout:** do not rerun setup or replace its venv/browser
while measuring session persistence. The corrected setup deliberately refuses a
venv that differs from `.python-version`. Apply source patches and run offline
tests in the existing environment; validate Python 3.12 separately as described in
[the cleanup workflow](docs/m1-cleanup.md).

```sh
./scripts/setup
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Setup uses the Python version in `.python-version` (currently 3.12.14) and
`uv sync --locked`. The generated `uv.lock` is the single authoritative dependency
lock, including distribution hashes; the old `requirements.lock` has been removed.
The four runtime versions are unchanged. `--locked` makes a stale lock an error
instead of silently updating it. See [the migration workflow](docs/uv-lock.md).
Do not run `uv sync` or `uv run` in the current Python 3.14 longevity-test checkout;
use its existing `.venv/bin/python` until that experiment is complete.

In PyCharm, open this directory and select `.venv/bin/python` as the interpreter.
Use this directory as the working directory. Either run `scripts/m1` in the
terminal, or configure a Python module run for `provadis_m1` with `src` marked as
Sources Root. Keep credentials out of run configurations and the repository.
For explicit server-side credential-assisted login, see
[headless login](docs/headless-login.md); it uses an owner-only file outside Git.

## Authenticate, then test reuse

```sh
./scripts/m1 login
./scripts/m1 fetch
./scripts/m1 verify
```

`login` opens a **dedicated Chromium profile**, waits up to 15 minutes for you to
sign in and open your timetable, then performs and validates a direct API GET.
Enter your password/MFA only in the portal window. The browser closes gracefully
after validation. Never paste credentials, codes, cookies, tokens, or HAR files
into chat or issues.

`fetch` starts a headless browser using that profile, opens the portal once to let
its own authentication code run, captures the authorization header from the exact
timetable GET, and then makes a direct API request without query parameters or a
body. It does not export or independently renew refresh tokens.

`verify` does this in **two separate headless browser lifetimes**, making at least
two direct requests in each. Success demonstrates short-term restart reuse and
unattended retrieval on that host. It does not establish long-term validity.

The default route is now the owner-confirmed `/student/schedule`. If it redirects
to the landing page, OTTER clicks the exact visible `Für Studierende` entry once
to start the portal's normal SSO flow. After the SSO round trip it uses
`Mein Studium` and `Stundenplan`, or re-enters
the visible student card once if the landing page remains. If the timetable route
returns to the authenticated dashboard before API retrieval, it permits one more
menu-navigation attempt within the same timeout; see
[dashboard recovery](docs/dashboard-retry.md). It no longer performs
a full-document timetable reload. See [post-SSO routing](docs/post-sso.md).
Normal login is interactive; fetch/verify never fill credentials. An explicit
`login --headless --credentials-file ...` can attempt the email/password forms
and the specifically identified stay-signed-in prompt. It does not automate
passkey/MFA, account selection, or application-permission consent prompts. It clicks the public
`Jetzt anmelden` button and the dedicated student SSO routing option; see
[the complete SSO workflow](docs/sso-navigation.md) and
[student-entry testing](docs/student-entry.md).

You can still override the same-origin route explicitly:

```sh
./scripts/m1 fetch --portal-path '/student/schedule'
```

Do not use an OAuth callback URL, query string,
authorization code, or token-bearing fragment. Only simple paths/hash routes are
accepted. Automatic routing clicks are bounded and restricted to the documented
portal/student SSO controls. Credential filling requires the separate explicit
headless-login option; see its limitations before using it.

For an opt-in redacted click/navigation logfile, add `--navigation-log`. The
result names a unique JSONL file under `.m1-state`; see
[recording and interpreting the navigation log](docs/navigation-log.md).

## Results and failures

Stdout contains one JSON summary, including counts and UTC completion time.
Event contents, user identity, credentials, raw upstream messages, and exception
details are omitted. The browser profile is `.m1-state/profile`, mode 0700,
excluded from Git. Only one process can use it at a time.

| Exit | Category | Meaning |
| --- | --- | --- |
| 0 | `validated_snapshot` | Every requested direct API sample passed validation |
| 2 | `authentication` | HTTP 401/403, unexpected API redirect, or browser closed during authentication |
| 3 | `network` | Portal navigation, API transport, or response read failed |
| 4 | `invalid_response` | Wrong status/content/schema/dates, `ok != true`, or count mismatch |
| 5 | `suspicious_response` | Structurally valid but empty timetable; no successful update |
| 6 | `configuration` | Unsafe/locked state, missing dependencies/browser/display, local error |
| 7 | `service` | HTTP 429 or 5xx; upstream temporarily unavailable |
| 8 | `bootstrap` | No usable timetable Bearer observed; authentication state is unknown |
| 130 | `interrupted` | Operator stopped the run |

HTTP 403 may mean insufficient permissions rather than expired login. A missing
observed request may mean the wrong portal route rather than expired login.
There are no automatic login loops. Every failed/suspicious result exits nonzero;
no timetable or calendar data is ever written in M1.

Validation requires HTTP 200, JSON, the confirmed envelope, strict `ok: true`, a
list with matching integer `count`, and nonempty titles plus valid offset-aware
start/end times with end after start. Building/room fields must exist but may be
null or empty. Extra fields are accepted; undocumented schema differences fail
closed and require review. Nonempty valid JSON still cannot prove that the
university returned a semantically complete timetable.

## Next steps

- [Development workflow, tests, rollback, and restart diagnosis](docs/development.md)
- [Authentication and reauthentication](docs/authentication.md)
- [Explicit credential-assisted headless login](docs/headless-login.md)
- [Safe SSH-only server testing](docs/server-testing.md)
- [Feasibility assessment and acceptance evidence](docs/feasibility.md)
- [Environment and publishing](docs/environment.md)
- [Native uv lock, clean-install verification, and CI](docs/uv-lock.md)

Before publishing, run `python3 scripts/check_public_tree.py` after staging the
intended files and review `git diff --cached`. Keep all browser profiles, raw
responses, HARs, screenshots, VNC passwords, and session backups outside Git.
Setup enables the versioned pre-commit guard using repository-local Git config.
