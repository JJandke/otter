# Authentication and reauthentication

For a successful login followed by failed fetch/verify, first follow
[restart diagnosis](development.md). A missing observed timetable request now
uses category `bootstrap`, exit 8, with safe diagnostics. It does not establish
that login expired. Keep the existing profile intact while investigating.

## What this prototype does

1. Acquire a nonblocking process lock on a private, marked M1 state directory.
2. Launch Playwright's bundled Chromium with a dedicated persistent profile and
   the Chromium sandbox enabled. Normal personal Chrome/Chromium profiles are
   never selected. No remote debugging port is opened.
3. Open the portal (or a confirmed same-origin timetable route). Let the existing
   website perform its own Entra B2C flow and any normal renewal.
4. Observe only a successful HTTP 200 for the exact GET to
   `https://api.provadis-hochschule.de/api/v1/Student/GetEventList` for the Bearer
   header. This lets the portal finish renewal if its first request used a stale
   token. Hold the header in memory, excluded from object reprs. No token is extracted
   from arbitrary storage keys, supplied on the command line, or exported.
5. Use the browser context's request client for a direct GET to that same fixed
   URL. Disable redirects and retries. Validate before declaring success.
6. Discard the in-memory header and gracefully close the browser. The browser's
   own profile remains private for the next run.

This deliberately avoids reconstructing an undocumented OAuth client. A browser
profile preserves browser-managed state, not a guarantee that every authentication
mechanism survives restart. Session storage in particular may not persist.

## Initial local login

Run `./scripts/m1 login` in your local terminal, then interact with the dedicated
Chromium window. Navigate to the timetable after login if necessary. The process
waits for a timetable Bearer request; it does not ask for a password or automate
MFA. A successful direct request closes the window and prints sanitized JSON.

If the portal requires a particular route, identify its simple path/hash route in
this browser and use `--portal-path` in subsequent commands. OAuth callback URLs
and URLs containing parameters are unsuitable. The default is `/student/schedule`.
The owner confirmed that this route redirects to the landing page before the
student flow is started. OTTER now tries the bounded navigation described in
[student-entry testing](student-entry.md); the owner demonstrated headless fetch and restart reuse on 24 November 2026.
See [the evidence and remaining gates](feasibility.md); longevity and server
compatibility remain open.

Then run `./scripts/m1 verify`. Both headless browser lifetimes must make valid,
nonempty direct requests. Preserve the sanitized result locally if desired:

```sh
# Run login first so the private directory already exists.
./scripts/m1 verify >> .m1-state/evidence.jsonl
```

Use the same `--portal-path` option on all commands if required. Exit 0 with
`restart_reuse_demonstrated: true` is the immediate acceptance evidence; it says
nothing about validity tomorrow. `browser_runs` contains successful earlier runs
even if a later restart fails; the top-level `ok` stays false in that case.

## Direct API access: precise scope

Within each browser lifetime, the direct samples use the captured Bearer header
without navigating through the UI between samples. Use:

```sh
./scripts/m1 fetch --samples 3 --sample-interval 10
```

Across process/browser restarts, this implementation first loads the portal to
let its own authentication code obtain a usable Bearer header. **Zero-navigation
API retrieval after a fresh process start is not implemented or demonstrated.**
This is a tradeoff: it avoids depending on token-cache internals or persisting a
second credential store. If portal initialization proves unreliable or expensive,
that is a feasibility concern to resolve before implementing the calendar.

The summary counts requests to the known identity host and its token endpoint,
without storing their URLs, headers, bodies, codes, or responses. These counters
are observations, not proof of a particular grant type, renewal, SSO, or MFA.

## Reauthentication

1. Stop any running M1 experiment using this profile. The process lock prevents
   overlapping runs; do not delete Chromium lock files to force access.
2. Run `./scripts/m1 login` again, on the **same host and OS account** that will
   fetch later. Use the same state directory and any confirmed portal path.
3. Complete the university's normal interactive login/MFA.
4. Run `./scripts/m1 verify` and preserve only the sanitized summary.

The server uses the tunneled GUI procedure in [server-testing.md](server-testing.md).
Do not copy a macOS profile or tokens to Linux; portability is not established and
may depend on OS key storage. No passwords or refresh tokens are accepted through
chat, CLI flags, environment files, or source configuration.

If a profile is unusable, leave it private for diagnosis or retire it with a
deliberate local operator action, then authenticate in a fresh dedicated M1 state
directory. Never use the old application's files as a fallback. Logging out and
MFA/revocation tests can affect the university session, so run those deliberately
as the account holder, after recording baseline behavior.

## Handling sensitive state

- Treat the entire profile as a credential. Owner-only permissions and umask 077
  protect local access; they are not encryption at rest. Use appropriate disk
  protection and the same trusted OS account.
- Do not enable Playwright debug logs, HAR recording, traces, video, screenshots,
  TLS key logs, or browser storage exports. The CLI rejects common debug/key-log
  environment switches; do not bypass that guard for a signed-in session.
- Neither browser console messages nor response bodies are forwarded to stdout.
  Exception messages are fixed strings because library errors can contain headers.
- Do not open the profile in a second Chromium process, commit it, upload it as a
  troubleshooting attachment, or include it in a source archive or backup to a
  public/shared destination.
- Run `scripts/check_public_tree.py` before publishing. Git ignore rules and the
  guard are defense in depth; neither can detect every kind of secret or personal
  information. Review the staged diff yourself.

References: [Playwright persistent contexts](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context),
[authentication and state](https://playwright.dev/python/docs/auth),
[API request context](https://playwright.dev/python/docs/api/class-apirequestcontext).
