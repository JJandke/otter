# M1: the complete public sign-in navigation

## Evidence and scope

The owner confirmed that clicking the student card reaches `/login/student` and
then waits for `Jetzt anmelden`. Public browser inspection independently verified
that button and the next Entra B2C choice page. That page has a dedicated
`Anmeldung für Studierende und Lehrende` button, separately from the generic
Microsoft sign-in option under `Zur Bewerbung oder Zertifikatsteilnahme`.
The dedicated student option led to a Microsoft sign-in screen; inspection stopped
there without entering account information. No live authenticated fetch was
performed by the assistant.

This patch clicks the dedicated student option. It intentionally does not choose
the generic applicant Microsoft option. It still uses the portal's own OAuth
client and policy; there is no independent OAuth integration or token replay.

## Bounded automatic routing

1. Open the public portal root.
2. Click its visible level-two `Für Studierende` heading once.
3. On the exact portal `/login/student` page, click the visible `Jetzt anmelden`
   button once.
4. On the exact HTTPS B2C origin, click the dedicated student/teacher routing
   button once, only after step 3. B2C authorize URLs normally have query
   parameters; their values are neither inspected nor reported.
5. Allow existing browser SSO to run. Never enter email/password, decline a
   passkey QR flow, approve MFA, select an account, or confirm stay-signed-in.
   Those actions remain with the operator in an interactive `login` run.
6. After a return to the portal, attempt the owner-reported unique visible
   `My Studium` and `Stundenplan` labels, each at most once. Their authenticated
   DOM is not verified here. If those labels are absent on the landing page, re-enter the visible
   student card once after the SSO routing round trip. The full-document timetable
   fallback has been removed; see [post-SSO routing](post-sso.md).
7. Validate a direct API response before success. The original time budget,
   no-login-loop behavior, and nonzero failure exits remain in place.

Portal OAuth callback query/fragment URLs are not interrupted. No credential or
MFA interactions are performed on Microsoft pages. A successful provider-choice
click does not prove authentication; expired/revoked sessions may still require
manual reauthentication. An incognito browser requiring a full login does not
establish how a persistent Chromium profile will behave.

The safe JSON now includes `sign_in_clicked`, `student_sso_clicked`,
`my_studium_clicked`, and `timetable_link_clicked`, in addition to existing flags.
A Microsoft login page is classified as `final_location: microsoft_login`, without
reporting its URL, account identity, or page content.

## Owner tests, review, commit, and rollback

This is incremental after the visible-heading fix, against owner commit `44a5502`.
Stay on the existing M1 diagnostic branch. The patch does not create commits,
change the existing profile, push, or deploy. Untracked patch downloads and
`scripts/setup.save` are not included.

After checking/applying the patch, run from the repository root in macOS Terminal,
PyCharm's terminal, or Ubuntu:

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
git diff --check
```

Expect 52 tests and `OK`. New regression tests cover the bounded sign-in button
and dedicated provider choice, callback protection, no interactions on Microsoft
credential pages, post-SSO return to the portal root, and the reported menu order.
These use mocks; passing is not proof of successful live SSO. In PyCharm, use a
Unittests configuration targeting `tests`, project root as working directory,
`.venv` as interpreter, and `PYTHONPATH=src` (or mark `src` as Sources Root).

Then run a fresh headless `./scripts/m1 fetch --bootstrap-seconds 120` without
interaction. If successful, run `./scripts/m1 verify --bootstrap-seconds 120`.
If necessary, observe `fetch --headed --bootstrap-seconds 120` untouched. If a real
credential/MFA prompt remains, use `./scripts/m1 login` and complete it yourself,
then rerun the headless checks. Do not share passwords, codes, or screenshots of
private pages. Preserve only safe JSON summaries. The same commands work on
Ubuntu; headed server runs need the temporary display/tunnel procedure already
documented. Do not alter production Nginx/systemd for this test.

Before committing, review `git diff` and stage only the seven intended files:
README.md, docs/student-entry.md, docs/sso-navigation.md,
src/provadis_m1/navigation.py, src/provadis_m1/browser.py,
tests/test_navigation.py, and tests/test_browser.py.
Run `scripts/check_public_tree.py`, then `git diff --cached --check` and inspect
`git diff --cached`. Suggested message:
`fix(auth): complete student sign-in routing before timetable retrieval`, with
`Refs #N` in the body, replacing N with the actual issue number. Do not close the
issue solely because unit tests pass. Review `git remote -v` and the commit before
any later push; deployment is outside M1.

Before staging, rollback with `git apply --reverse --check <patch>` followed by
`git apply --reverse <patch>`. If already committed, `git revert <commit-id>`
records an inverse change. Preserve uncommitted user work and browser state; do
not use `reset --hard` or `git clean -fdx`. Longer-running renewal and Ubuntu
acceptance remain pending even after a successful short-term verify.
