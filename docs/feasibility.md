# M1 feasibility assessment

**Assessment on 24 November 2026: local short-term authentication feasibility
has been demonstrated by the owner; M1 remains open.** Headless fetch validated
80 events, followed by two headless browser lifetimes with two direct samples each.
Both lifetimes exercised the bounded dashboard retry. No identity-host requests
were observed during the direct samples. A fresh browser still bootstraps the
portal; zero-navigation retrieval after a process restart is not demonstrated.

Read-only inspection found the owner's current `.venv` uses Python **3.14.7**,
while `.python-version` and `pyproject.toml` declare the Python **3.12** target
(pin 3.12.14). Do not label the live evidence as Python 3.12 acceptance. The
assistant has installed the locked dependencies in a separate clean Python 3.12.14
environment and passed its offline tests. Live target-version validation, Ubuntu
testing, longevity, idle recovery, and reauthentication evidence remain outstanding. Preserve the working profile and
runtime during the existing measurement series.

### Sanitized owner-reported live evidence

All times below are Europe/Berlin (CET), 24 November 2026. These are completion
times, not the time of initial interactive authentication.

| Run | Completion | Validated samples | Navigation recovery |
| --- | --- | --- | --- |
| Headless `fetch` | 11:43:24 | One direct sample, 80 events | No retry needed |
| Headless `verify`, first lifetime | Combined command finished 11:44:59 | Two direct samples, 80/80 events | One dashboard retry |
| Headless `verify`, second lifetime | Combined command finished 11:44:59 | Two direct samples, 80/80 events | One dashboard retry |

The top-level verification reported both restart reuse and unattended retrieval.
Keep the original summaries and navigation logs private. The counts and results
above are sufficient public evidence; no state identifiers, personal event data,
identity-provider URLs, credentials, or raw log files belong in Git.

## Evidence and acceptance gates

| Requirement | Evidence available | Status |
| --- | --- | --- |
| Clean isolated Python project with Git | Isolated repository and pinned dependencies; current owner venv is Python 3.14.7 | Implemented; clean Python 3.12 validation open |
| GitHub repository | Owner checkout has origin `https://github.com/JJandke/otter` | Remote configured; publication/CI not independently verified |
| Valid authenticated API response | Initial login: 82 events; later headless fetch/verify: 80 events | **Demonstrated in the owner's local run** |
| Session reuse after restart | Two successful separately launched headless browsers | **Demonstrated locally, short term** |
| Unattended retrieval | Owner-reported headless fetch and verify completed without intervention | **Demonstrated locally, short term** |
| Direct retrieval without repeated full UI navigation | Two direct samples per browser; no observed identity activity during those samples; each fresh browser still bootstraps the portal | Demonstrated within each browser lifetime |
| Credential/session protection | In-memory Bearer, private profile/lock, safe summaries, ignore rules, publication guard | Implemented; human review still required |
| Invalid/missing responses cannot count as successful updates | Nonzero exit for all invalid/suspicious responses; no calendar writes exist | Covered by offline tests |
| Isolation from old application | Dedicated paths, protected-path check, no production configuration code | Implemented; server untouched |
| Python 3.12 / Ubuntu compatibility | Fresh locked Python 3.12 installation and offline tests pass on macOS; live target-version and server checks remain | Open |
| Longer-running renewal tests | Matrix and decision gates below; owner is measuring persistence | Results pending |

## What is confirmed, and by whom

The project brief confirms the portal and fixed authenticated GET endpoint, the
response envelope/known event fields, explicit date offsets, the Bearer header,
and Entra B2C host/policy `b2c_1a_hvs_signup_signin`. It reports an observed
authorization-code/PKCE and refresh-token flow. These facts have not been
independently re-demonstrated by the assistant; the owner has now demonstrated
interactive and short-term unattended direct retrieval using this implementation.

No original code archive is necessary for a new isolated M1 prototype. No HAR,
token, or old-app file was imported into the repository. The owner confirmed the
timetable route `/student/schedule` and its navigation
controls. The accepted payloads satisfy the implemented schema checks; broader
field nullability, completeness, `count` semantics, renewal behavior, and the
persisted session mechanism remain incompletely characterized.

## Design decision and limits

Use the original portal as the authentication client, in its own persistent
Chromium profile. Capture only a timetable request's Bearer header in memory,
then use the browser context's direct request client. This uses the account
holder's existing portal access without pretending to have a supported
independent OAuth registration.

Cookie sharing alone does not create a Bearer header. A persistent profile alone
does not promise usable access tokens after restart. The portal bootstrap is
therefore explicit on every fresh run. The confirmed portal controls and one
bounded dashboard recovery are implemented. Their short-term success does not
establish stability after future portal changes. Failure to observe the request
is inconclusive about the underlying reason.

The prototype does not read token-cache internals, copy refresh tokens between
hosts, implement a custom OAuth client, claim device-code support, or promise
indefinite sessions. Direct requests stop on HTTP 401/403; the original website
gets another opportunity to renew only on the next run. No hidden retry loop can
turn a failure into an apparently successful calendar update.

## Main unresolved risks

1. **Renewal beyond one day.** Microsoft documents a 24-hour refresh-token
   lifetime for B2C single-page apps using authorization code/PKCE. The portal
   registration type has not been verified here. Even if that limit applies,
   top-level browser SSO might recover without credentials; tenant session and
   MFA policy determine whether this works. Testing past 24 hours is mandatory.
2. **Session persistence and revocation.** B2C cookies, app storage, OS key
   protection, sign-in frequency, MFA, logout, password changes, and administrator
   revocation can all change the result. A short successful restart proves only
   that particular restart on that host/account.
3. **Unattended navigation.** The current bounded flow works locally, including
   recovery after a timetable-to-root return. The portal-side cause of that return
   is not established. Portal changes, bot checks, or consent screens may
   prevent unattended access even with otherwise valid state.
4. **Target runtime and server compatibility.** Live Python 3.12 behavior remains
   unproven because the current working environment is Python 3.14.7. Chromium
   launch, sandbox behavior, resource usage,
   headed login through the tunnel, and headless reuse must be verified on
   `franz`. Cross-host profile portability is deliberately excluded.
5. **API completeness.** Strict validation catches structural failures, not a
   plausible but incomplete nonempty response, wrong semester, or changed time
   horizon. No data loss prevention algorithm for real calendars belongs in M1;
   later milestones need independent snapshot sanity rules.
6. **Unsupported integration.** The university has not supplied a supported
   independent OAuth integration. Technical success does not ensure the portal
   behavior or access arrangement will remain supported. Clarify with university
   IT before relying on it as a long-term service.

References, checked 23 November 2026:
[B2C token configuration](https://learn.microsoft.com/en-us/azure/active-directory-b2c/configure-tokens),
[B2C token overview](https://learn.microsoft.com/en-us/azure/active-directory-b2c/tokens-overview),
[Playwright authentication](https://playwright.dev/python/docs/auth),
[supported systems](https://playwright.dev/python/docs/intro).

The owner's first headless server fetch on 24 November reached Microsoft login
with zero timetable requests and zero observed token-endpoint requests. This is
evidence that headless Chromium launch and the initial portal/SSO route work on
that server. It does not establish authenticated API access or session reuse.

## Optional headless initial login (implemented, not live-validated)

`login --headless --credentials-file <private-file>` can make one Microsoft
email/password submission and choose stay-signed-in only when its specific form
marker is present. It does not automate MFA/passkeys or consent. The owner asked
for this M1 experiment after the server reached Microsoft login with no session.
The recognized form IDs are implementation assumptions validated by synthetic
tests; live university/server validation is still required. Timeout remains a
failed bootstrap, never a validated update or proof of incorrect credentials.

This adds a plaintext university-password file while in use. Keep it owner-only
and outside all Git checkouts; remove it after initial authentication if ongoing
retention is unnecessary. It is more sensitive than a narrow timetable credential.
Normal fetch and verify never load it, even if it remains present. Preserve the
original browser-only/session-reuse evidence and record this separate initial
login method accurately. See [headless-login.md](headless-login.md).

## Outstanding foundation work

Setup now reads the required version from `.python-version`, rejects a mismatched
existing venv before installation, and does not replace it automatically. Validate
Python 3.12 in a separate checkout/profile after choosing when to start its own
session series. Native `uv.lock` migration and a clean macOS Python 3.12 dependency installation
are complete. The updated Ubuntu CI workflow still needs a verified green run.
See [uv-lock.md](uv-lock.md) for reproduction and review. No runtime/browser upgrade is part
of this documentation cleanup.

## Live test matrix

Use the same dedicated profile, OS account, pinned browser, and confirmed portal
path throughout each series. Run a **fresh process** for each timed sample.
Record actual elapsed time since interactive login and since the most recent
successful fetch. Do not infer token age or initial authentication time from the
latest summary timestamp. A target-runtime series must be recorded separately
from the current working-runtime series.
Preserve only exit codes and sanitized JSON. Never store raw tokens to measure
expiry.

| Test | Procedure | Evidence needed |
| --- | --- | --- |
| Initial baseline | `./scripts/m1 login` | Exit 0 and a validated nonempty direct sample |
| Immediate restart + unattended | `./scripts/m1 verify` | Two headless lifetimes; at least two valid direct GETs each |
| UI-independent direct reads | `fetch --samples 3 --sample-interval 10` | Three direct successes; counters show any identity activity |
| After likely access-token expiry | Fresh `fetch` at +1 h and +2 h | Continued valid responses; no operator input; record identity counters |
| Across SPA refresh boundary | Fresh `fetch` at +23 h, +25 h, +48 h, +72 h | Confirm or refute unattended recovery after the documented one-day boundary |
| Ongoing operation | Fresh hourly `fetch` manually supervised for 7 days, then checks at 14/30 days | Success rate, outage windows, reauth count, no silent failures |
| Idle recovery | Separate series on the same dedicated profile: stop all M1 runs for 25 h, then fresh headless `fetch` | No intervening login, fetch, verify or browser activity; record the actual idle interval |
| Host reboot | After approval for an actual server reboot, fresh `fetch` using same profile | Session and profile survive OS restart; no GUI dependency |
| Reauthentication drill | After an actual auth failure, rerun `login`, then `verify` | Operator can restore access without touching production |
| Controlled revocation | Account holder deliberately logs out/revokes an M1 session, where appropriate | Nonzero failure, no reported successful snapshot; documented recovery |

The hourly series is a **test definition**, not an installed schedule. Run it in
an isolated terminal experiment only when ready; M1 adds no cron/systemd job.
Pause on repeated failures, HTTP 429, or unexpected portal behavior. Do not hammer
login or token endpoints. A 7-day pass is encouraging evidence, not proof of
indefinite unattended validity.

## Proceeding to M2

Proceed only after a clean target-Python environment, baseline, restart,
unattended retrieval, past-24-hour and idle-recovery testing succeed on the target host, and the observed renewal behavior and
reauthentication burden are acceptable. If unattended recovery repeatedly fails,
decide whether manual reauthentication is acceptable or seek a supported
integration from university IT before building the rest.

Future SQLite history, event matching, cancellation rules, ICS output, Nginx
serving, and systemd scheduling remain unimplemented. A structurally valid M1
response is not yet an authorization to modify future calendar data.
