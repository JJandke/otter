# M1 feasibility assessment

**Assessment on 23 November 2026: partially demonstrated by the owner.**
Interactive login and a direct API GET validated 82 events. Subsequent headless
fetch and verify attempts did not obtain a timetable Bearer request, including
an attempt with `/#/student/schedule`. The cause is still unknown; session reuse
and unattended operation remain open acceptance gates. See
[the diagnostic workflow](development.md) for the evidence and next tests.

## Evidence and acceptance gates

| Requirement | Evidence available | Status |
| --- | --- | --- |
| Clean isolated Python project with Git | New `main` repository, Python 3.12.14 venv, pinned dependency closure | Implemented locally |
| GitHub repository | Owner checkout has origin `https://github.com/JJandke/otter` | Remote configured; publication/CI not independently verified |
| Valid authenticated API response | Owner-reported `login` summary: 82 validated events, one direct sample | **Demonstrated in the owner's local run** |
| Session reuse after restart | Owner's headless `fetch` and `verify` failed to observe a timetable Bearer request | **Not demonstrated; diagnosis required** |
| Unattended retrieval | `fetch`/`verify` do not ask for input or automate MFA | **Not demonstrated live** |
| Direct retrieval without repeated full UI navigation | Direct GET function tested; same-process samples need no extra navigation; each fresh process still bootstraps the portal | Partially implemented; live behavior open |
| Credential/session protection | In-memory Bearer, private profile/lock, safe summaries, ignore rules, publication guard | Implemented; human review still required |
| Invalid/missing responses cannot count as successful updates | Nonzero exit for all invalid/suspicious responses; no calendar writes exist | Covered by offline tests |
| Isolation from old application | Dedicated paths, protected-path check, no production configuration code | Implemented; server untouched |
| Longer-running renewal tests | Matrix and decision gates below | Defined, not run |

## What is confirmed, and by whom

The project brief confirms the portal and fixed authenticated GET endpoint, the
response envelope/known event fields, explicit date offsets, the Bearer header,
and Entra B2C host/policy `b2c_1a_hvs_signup_signin`. It reports an observed
authorization-code/PKCE and refresh-token flow. These facts have not been
independently re-demonstrated by the assistant; the owner has now demonstrated
interactive direct retrieval using this implementation.

No original code archive is necessary for a new isolated M1 prototype. No HAR,
token, or old-app file was imported into the repository. The exact timetable SPA
route, actual field nullability/types, `count` semantics, refresh behavior, and
persisted session mechanism still need live confirmation.

## Design decision and limits

Use the original portal as the authentication client, in its own persistent
Chromium profile. Capture only a timetable request's Bearer header in memory,
then use the browser context's direct request client. This uses the account
holder's existing portal access without pretending to have a supported
independent OAuth registration.

Cookie sharing alone does not create a Bearer header. A persistent profile alone
does not promise usable access tokens after restart. The portal bootstrap is
therefore explicit on every fresh run. If the app uses session storage or requires
clicking into a timetable view, a confirmed route or further narrow portal
integration may be needed. Failure to observe the request is inconclusive about
the underlying reason.

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
3. **Unattended navigation.** Root-page initialization and a stable timetable
   route are not established. Portal changes, bot checks, or consent screens may
   prevent unattended access even with otherwise valid state.
4. **Server compatibility.** Chromium launch, sandbox behavior, resource usage,
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

## Live test matrix

Use the same dedicated profile, OS account, pinned browser, and confirmed portal
path throughout each series. Run a **fresh process** for each timed sample.
Record actual elapsed time since interactive login, not just wall-clock labels.
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
| Idle recovery | Separate series: login, stop all M1 browsers for 25 h, then fresh headless `fetch` | Recovery with no running-page heartbeat |
| Host reboot | After approval for an actual server reboot, fresh `fetch` using same profile | Session and profile survive OS restart; no GUI dependency |
| Reauthentication drill | After an actual auth failure, rerun `login`, then `verify` | Operator can restore access without touching production |
| Controlled revocation | Account holder deliberately logs out/revokes an M1 session, where appropriate | Nonzero failure, no reported successful snapshot; documented recovery |

The hourly series is a **test definition**, not an installed schedule. Run it in
an isolated terminal experiment only when ready; M1 adds no cron/systemd job.
Pause on repeated failures, HTTP 429, or unexpected portal behavior. Do not hammer
login or token endpoints. A 7-day pass is encouraging evidence, not proof of
indefinite unattended validity.

## Proceeding to M2

Proceed only after baseline, restart, unattended retrieval, and past-24-hour
testing succeed on the target host, and the observed renewal behavior and
reauthentication burden are acceptable. If unattended recovery repeatedly fails,
decide whether manual reauthentication is acceptable or seek a supported
integration from university IT before building the rest.

Future SQLite history, event matching, cancellation rules, ICS output, Nginx
serving, and systemd scheduling remain unimplemented. A structurally valid M1
response is not yet an authorization to modify future calendar data.
