# Direct API transport recovery — 25 November 2026

Base: clean `fix/m1-session-diagnostics`, commit
`a33da4c3c684dfae18b90ff249bbc311660004ba`. This is incremental to the
detached-submit patch already installed there. No original checkout, profile,
credential file, branch or commit was changed by preparation of this patch.

## Evidence

Archive.zip contains two reports, twelve navigation logs (1,478 events), and one
sanitized failure report. The earlier seven-step smoke passed. In the repeated
run, fresh login and the first three wet fetches passed. The fourth wet fetch
failed; the second fresh cycle had not started.

The failure report shows timetable state, a captured bearer, one portal API
response with HTTP 200, zero credential attempts and no direct HTTP status.
The failing navigation log records a direct API attempt at 13:22:01.937 UTC and
failure at 13:22:23.550 UTC. The request threw before a response was returned.
The existing code converts all request exceptions to a generic network reason,
which the acceptance projection further replaces with `unclassified_child_failure`.
The logs cannot distinguish a timeout, connection reset, TLS error or another
transport failure. The configured default request timeout is 30 seconds;
the measured 21.6 seconds alone does not establish a timeout.

The archive also includes browser-profile files. These were not extracted or
inspected. Share only reports, sanitized failure artifacts and navigation logs
in future; profiles contain session secrets. No archive content is included in
this patch.

## Change

Only the fixed read-only timetable GET gains retries: three attempts maximum,
each limited by the existing request timeout, under one shared deadline of three
request budgets. With the default 30 seconds this means at most approximately
90 seconds per direct sample. Retries have no fixed sleeps, no redirects and no
Playwright internal retries. Timeout, connection-reset/aborted socket and
temporary DNS-resolution errors are eligible. Unknown, TLS-certificate and
closed-context failures fail immediately. Persistent failures still fail nonzero.

HTTP responses (including 401, 403 and 5xx), malformed responses and body-read
failures do not enter this transport retry path. Existing bounded 401 portal
recovery remains separate. No login/credential action is retried by this change.
All successful responses still undergo the existing HTTP, content-type, schema,
event/count and size validation. Seeing portal HTTP 200 alone remains insufficient.

Diagnostics now record `direct_api_attempts` and
`direct_api_transport_failures`; acceptance reports retain these integer counts.
`api_transport_retry` logs only a fixed error category, never exception text,
headers or bodies. Final reasons `direct_api_timeout`,
`direct_api_transport_failed` and `response_read_failed` survive projection.
The child-process watchdog accounts for three 30-second attempts per sample,
including both samples in both verify browser lifetimes and one existing
401-recovery replay per lifetime.

## Verification and owner retest

Offline tests cover reset-then-success with validated synthetic data, finite
timeout exhaustion, shared deadlines, non-retryable errors, no HTTP-status
retries, response disposal, sanitized logs and acceptance reasons. These mocks
cannot reproduce the university server or identify the original transport cause.
The Python 3.12.14 offline suite passed: 179 discovered, 171 passed and eight
opt-in browser tests skipped. Live Microsoft/Provadis smoke was not run by the assistant. Browser fixtures are
unchanged from the prior patch; their earlier startup limitation remains.

Keep the current M1 fix branch. Download the patch outside the checkout:

```sh
cd "$HOME/Downloads/otter"  # Ubuntu: use your existing checkout path
patch_file="$HOME/Downloads/OTTER_M1_API_Transport_Recovery.patch"
git status --short --branch
git log -1 --oneline
git apply --stat "$patch_file"
git apply --check "$patch_file"
git apply "$patch_file"
git diff --check
```

Use your existing Python 3.12.14 `$m1_python`, then run on Mac and Ubuntu:

```sh
"$m1_python" --version
PYTHONPATH=src "$m1_python" -m unittest discover -s tests -v
credentials_file="$HOME/.config/otter/credentials.env"
PYTHONPATH=src "$m1_python" -m provadis_m1.acceptance smoke \
  --credentials-file "$credentials_file" --decline-passkey \
  --fresh-cycles 2 --wet-runs 5
```

A complete repeated smoke has 18 passed steps. Review the fresh password POST
observations, zero credential attempts in strict wet steps, validated API counts
and transport-attempt/failure counts. If recovery occurs, the report should
show more direct attempts than samples and a nonzero transport-failure count.
If the next run still fails, its explicit reason and sanitized counts should
narrow the cause. Do not loosen API validation to make the run pass.

In PyCharm use the same interpreter, repository working directory and
`PYTHONPATH=src` for unittest discovery. Run live acceptance as module
`provadis_m1.acceptance` with the same arguments. Do not save credentials in
shared run configurations.

Rollback before further overlapping edits:

```sh
git apply --reverse --check "$patch_file"
git apply --reverse "$patch_file"
```

After review and successful live retesting, stage the five changed paths shown
by `git apply --stat`, inspect `git diff --cached`, run the publication guard,
and use `fix(api): recover transient timetable transport failures` with
`Refs #ISSUE_NUMBER` in the commit body. Replace the placeholder with your
existing M1 issue number. No commit is created by applying this patch. Keep M1
open until the final code passes the smoke and active/idle recovery gates.
