# OTTER — Organised Timetable Translation and Event Retrieval

Milestone M1: project foundation and authentication feasibility.

An isolated Python prototype for **authentication feasibility**, with no calendar,
database, public endpoint, or production job. It leaves the old application and
existing Nginx/systemd configuration alone.

**Status:** the owner reported a validated live login response with 82 events on
23 November 2026. Subsequent fetch/restart attempts failed to obtain a timetable
Bearer request, including a run using `/#/student/schedule`. Session restart
reuse, unattended access, and server behavior remain unproven.
Do not treat M1 as accepted until the live checks in
[docs/feasibility.md](docs/feasibility.md) have recorded successful results.

## Local setup (macOS / PyCharm)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git.
Then, from this project directory:

```sh
./scripts/setup
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

The setup pins Python 3.12.14 and all four runtime packages in
`requirements.lock`. This is a pip-compatible lockfile used by `uv pip sync`;
there is no generated native `uv.lock` yet. The lockfile pins versions but does
not contain distribution hashes. See [the environment notes](docs/environment.md)
for the remaining reproducibility checks. Use `./scripts/m1`, not `uv run`,
which would create a different native project lock.

In PyCharm, open this directory and select `.venv/bin/python` as the interpreter.
Use this directory as the working directory. Either run `scripts/m1` in the
terminal, or configure a Python module run for `provadis_m1` with `src` marked as
Sources Root. No credentials belong in run configurations or environment files.

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
to start the portal's normal SSO flow. After a return to another student page,
it waits two seconds and navigates to the schedule at most once. It never fills
credentials or handles passkey/MFA/confirmation prompts. It clicks the public
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
portal/student SSO controls; credentials remain manual.

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
- [Safe SSH-only server testing](docs/server-testing.md)
- [Feasibility assessment and acceptance evidence](docs/feasibility.md)
- [Environment and publishing](docs/environment.md)

Before publishing, run `python3 scripts/check_public_tree.py` after staging the
intended files and review `git diff --cached`. Keep all browser profiles, raw
responses, HARs, screenshots, VNC passwords, and session backups outside Git.
Setup enables the versioned pre-commit guard using repository-local Git config.
