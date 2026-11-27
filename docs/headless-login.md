For visible credential tests, passkey refusal, current diagnostics and fresh-profile
reset instructions, see [login-form-tests.md](login-form-tests.md).

# M1: explicit credential-assisted headless login

## Scope and limitations

This is initial authentication feasibility, so it belongs to M1. The feature is
opt-in and uses the existing portal/browser flow. It does not call Microsoft
with a password grant, register an OAuth client, replay refresh tokens, or change
university policies. No credential values are accepted as command arguments.

Only this command reads the credential file:

```sh
./scripts/m1 login --headless --credentials-file /absolute/private/credentials.env
```

The recognized Microsoft controls are `loginfmt`, password input `passwd`, and
submit control `idSIButton9`. A stay-signed-in click additionally requires the
`KmsiDescription` marker. These selectors are assumptions about the Microsoft UI,
not a supported authentication API; no live successful credential-assisted login
is claimed yet. Each form is submitted at most once per explicit command.

Credentials are filled only at the exact HTTPS `login.microsoftonline.com`
origin. Redirects to another provider, account-picker-only or password-only
screens, passkeys, MFA and application-permission consent are not automated.
The command waits within the existing bootstrap deadline; an unsupported prompt
may therefore end as `headless_login_incomplete`, bootstrap exit 8. It does not
infer an incorrect password, expired session or mandatory MFA from a timeout.
Do not repeat failed password attempts in a loop.

Success still requires a real direct API GET with HTTP 200 and strict response
validation. Filling forms or reaching the timetable URL is never success. Normal
`fetch` and `verify` do not read the file or attempt password login, and reject
the credential-file/headless-login flags. They continue to reuse the browser
profile. The feature does not modify the current Mac longevity-test profile.

## Source workflow and offline verification

Continue `fix/m1-session-diagnostics`. This incremental patch is based on
`746f632`. The assistant prepared and tested an isolated source copy. It did not
read any real credential file, use a university password, operate a signed-in
browser, modify the owner's checkout, or install anything on `franz`.

After downloading the patch:

```sh
cd /Users/Johannes/Downloads/otter
git status --short --branch
git apply --check ~/Downloads/OTTER_M1_Headless_Login.patch
git apply ~/Downloads/OTTER_M1_Headless_Login.patch
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

Expect **96 tests, OK**. New tests cover file ownership/permissions, symlink/FIFO
rejection, parsing without shell expansion, redaction, origin restrictions,
ambiguous controls, no automatic credential loading, bounded form submission,
integration with existing validated retrieval and unsuccessful bootstrap handling.
No test reads real credentials or visits Microsoft. No dependency/lock upgrade
is needed. Use the same commands in PyCharm's terminal or the isolated Ubuntu
checkout. For PyCharm's GUI runner use Unittests targeting `tests`, the existing
interpreter, project-root working directory and `PYTHONPATH=src`.

Review `git diff`, then stage only these files:

```sh
git add src/provadis_m1/credentials.py src/provadis_m1/password_login.py \
  src/provadis_m1/__main__.py src/provadis_m1/browser.py \
  src/provadis_m1/navigation_log.py scripts/check_public_tree.py .gitignore \
  tests/test_credentials.py tests/test_password_login.py tests/test_cli.py \
  tests/test_browser.py README.md docs/authentication.md docs/server-testing.md \
  docs/environment.md docs/feasibility.md docs/headless-login.md
.venv/bin/python scripts/check_public_tree.py
git diff --cached --check
git diff --cached
```

After review, use a meaningful commit (replace N with the actual issue number):

```sh
git commit -m "feat(auth): add explicit credential-assisted headless login" \
  -m "Refs #N"
```

Before publishing, review the commit, `git status`, and `git remote -v`; push only
the reviewed issue branch when ready. No deployment is performed by these commands.
On the server update the isolated M1 source to that reviewed commit before using
the new options. Run offline tests there too; preserve existing `.m1-state`.

## Create the credential file locally on franz

Use the same non-root account that runs the isolated M1 experiment. Choose a
private directory outside all Git checkouts and the preserved old application:

```sh
umask 077
mkdir -p "$HOME/.config/otter"
chmod 700 "$HOME/.config/otter"
touch "$HOME/.config/otter/credentials.env"
chmod 600 "$HOME/.config/otter/credentials.env"
nano "$HOME/.config/otter/credentials.env"
```

In that local editor, enter exactly these two keys, replacing the placeholders
with your real values only on the server. Never paste the completed file into
chat, a Git issue, a command argument, or a screenshot:

```dotenv
OTTER_USERNAME='REPLACE_WITH_UNIVERSITY_EMAIL'
OTTER_PASSWORD='REPLACE_WITH_PASSWORD'
```

The parser accepts a deliberately small dotenv subset: blank/full-comment lines
and the two unique `KEY=value` assignments. Quote values containing whitespace.
Single/double shell-style quotes are supported; no `export`, substitutions,
multiline values or inline comments. `$`, `#` and command-looking text inside
quotes remain literal; the file is not executed or exported. Escape/quote any
quote characters deliberately. Files must be regular, owned by the current
account, inaccessible to other users, and outside the project/old application.
Symlinks are rejected. No values or parse-error contents are printed.

This is a plaintext account password, not encrypted storage. Remove the private
file after login if retention is unnecessary; the browser profile alone is used
for subsequent retrieval. The ignore rules and publication guard reject `.env`
files, but keeping the file outside all repositories is the primary separation.

## Run the live experiment on franz

From the isolated server checkout, after the file is saved:

```sh
umask 077
set -o pipefail
./scripts/m1 login --headless \
  --credentials-file "$HOME/.config/otter/credentials.env" \
  --bootstrap-seconds 120 --navigation-log \
  | tee .m1-state/headless-login-summary.json
```

The `tee` examples assume `.m1-state` was initialized by the earlier server fetch.
For a completely fresh state, omit `tee` on the first command; do not create
unmarked state contents merely to capture its output. Do not start another M1
process at the same time. If it returns a validated
snapshot, verify session reuse without supplying the file:

```sh
./scripts/m1 verify --bootstrap-seconds 120 --navigation-log \
  | tee .m1-state/server-verify-summary.json
```

Both headless lifetimes must validate their direct API samples. Record which
initial login method was used and the actual authentication time. If phone
approval was required, record that the initial login was assisted.

Safe diagnostics distinguish `credential_email_attempted` /
`credential_password_attempted` from completed `credential_email_submitted` /
`credential_password_submitted`, and include `keep_signed_in_clicked`. An opt-in log records only fixed step names, not
values, labels, DOM, storage or error text. Do not enable DEBUG/PWDEBUG, browser
traces, HARs, TLS key logs or screenshots of signed-in pages.

If the command stops with exit 8, preserve the safe summary and named navigation
log. No submitted fields means the expected forms were not observed; email only
may mean a passkey/account-choice/federation prompt; password submission without
an API response has several possible causes. None proves which prompt appeared.
Use an interactive method if needed; never disable MFA or add broad click rules
merely to force headless success. `configuration` exit 6 can indicate file safety,
missing browser/dependencies or invalid option combinations. No production Nginx,
systemd or old-application changes are needed.

## Rollback

Before staging, check and apply the reverse patch (`git apply --reverse --check`
then `git apply --reverse`). If staged, unstage only these paths first. After
committing use `git revert <commit-id>` and rerun tests. Source rollback does not
delete private credential/profile files; manage those deliberately as the owner.
No `git reset --hard`, forced branch switch or `git clean` is required.

References: [Playwright authentication](https://playwright.dev/python/docs/auth),
[Microsoft MFA](https://learn.microsoft.com/en-us/entra/identity/authentication/concept-mfa-howitworks),
[bound DOM handles](https://playwright.dev/python/docs/api/class-elementhandle).
