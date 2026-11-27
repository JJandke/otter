# M1: visible credential login, passkey refusal and disposable profiles

This incremental patch is based on `e0bfc2c`. Continue the existing
`fix/m1-session-diagnostics` branch. No dependency upgrade, production change,
real credential-file read or real university login was performed by the assistant.

## Behavior and honest limits

`login --headed --credentials-file ...` now uses the same automation as headless
credential login while displaying Chromium. Ordinary interactive login is still
available without a credential file. `fetch` and `verify` remain browser-state
only. Both mode flags together, missing mode or credential flags on fetch are
rejected. MFA/permission consent are not automated.

The Microsoft form detector logs fixed email/password visible-match counts when
they change. Both controls appearing no longer immediately fails: email is
submitted first, followed by the password stage. Duplicate matches still fail.
These are implementation hypotheses that need validation against the live page.
Existing exact-origin and bound-DOM-element checks protect credential filling.

`--decline-passkey` installs an origin-restricted initialization script that
rejects Microsoft `navigator.credentials.get({publicKey: ...})` requests with
`NotAllowedError`. It does not click a native dialog; it declines the request
before that dialog opens. It is not a real assertion, fake passkey, or MFA bypass.
Microsoft must still offer and accept password authentication. Other origins and
non-WebAuthn credential requests retain their original behavior. The init script
is installed only for this explicit command; it is not a persistent browser
setting. `passkey_decline_enabled` means the mode is installed, not that a request
was observed or that fallback succeeded. This remains an experimental adaptation.

The existing dedicated student SSO option remains in use, rather than a generic
Microsoft option in the applicant section. The portal starts at its landing page
and uses the already-tested clicks through student login and timetable navigation.
API success still requires strict validation; a URL or submitted password is not
successful authentication evidence.

## Apply, test, review and commit

On the Mac, after saving the patch to Downloads:

```sh
cd /Users/Johannes/Downloads/otter
git status --short --branch
git apply --check ~/Downloads/OTTER_M1_Login_Forms.patch
git apply ~/Downloads/OTTER_M1_Login_Forms.patch
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

Expect **99 tests, OK**. They cover combined-control sequencing, duplicate-control
rejection, visible automation and CLI option isolation as well as the prior
credential-file, redaction, navigation and API tests. The refusal JavaScript was
also exercised separately with synthetic navigator/origin objects. These checks
do not establish live Microsoft fallback. In PyCharm use the same terminal
commands or Unittests targeting `tests`, project-root working directory,
`PYTHONPATH=src` and the current interpreter. Ubuntu uses the same offline checks.

Review and stage only:

```sh
git add src/provadis_m1/__main__.py src/provadis_m1/browser.py \
  src/provadis_m1/password_login.py src/provadis_m1/passkey.py \
  src/provadis_m1/navigation_log.py tests/test_password_login.py tests/test_cli.py tests/test_browser.py \
  scripts/check_public_tree.py .gitignore docs/login-form-tests.md \
  README.md docs/headless-login.md docs/environment.md
.venv/bin/python scripts/check_public_tree.py
git diff --cached --check
git diff --cached
```

After review, commit with `Refs #N` (actual issue number) in the body:
`feat(auth): add visible login diagnostics and opt-in passkey refusal`.
Publish the reviewed issue branch and update the isolated server checkout only
after the Mac tests. No automated push/deployment is part of this patch.

## Fresh GUI test on the Mac

Keep the real credential file outside Git as described in `headless-login.md`.
Create it independently on the Mac if it currently exists only on the server;
do not send its contents through chat. The example below uses its protected path.
Use an existing working Python/browser environment; do not replace the runtime
of the ongoing longevity experiment.

```sh
umask 077
set -o pipefail
mkdir -p .m1-login-tests
chmod 700 .m1-login-tests
./scripts/m1 login --headed --decline-passkey \
  --credentials-file "$HOME/.config/otter/credentials.env" \
  --state-dir "$PWD/.m1-login-tests/gui-01/.m1-state" \
  --bootstrap-seconds 180 --navigation-log \
  | tee .m1-login-tests/gui-01-summary.json
```

Watch without clicking first. If manual intervention is needed, record it as an
assisted diagnostic; do not call that run unattended. The log name in the summary
lives under `.m1-login-tests/gui-01/.m1-state/`. The summary is saved outside that
state directory so `tee` cannot interfere with first-time state initialization.
Logs flush after each event and close on failure. Do not enable raw DEBUG,
Playwright traces/HARs or browser dumps containing entered values.

Use `gui-02`, `gui-03`, etc. for genuinely fresh attempts. These runs initialize
a separate browser profile every time; they do not use or reset the default
`.m1-state/profile` used for the longevity test.

## Headless and server tests

After a fully automatic GUI success, repeat the same command with `--headless`
instead of `--headed` and a new state path such as
`.m1-login-tests/headless-01/.m1-state`. Change the summary filename accordingly.
After successful login, run `verify` on that SAME state path without credentials:

```sh
./scripts/m1 verify \
  --state-dir "$PWD/.m1-login-tests/headless-01/.m1-state" \
  --bootstrap-seconds 180 --navigation-log \
  | tee .m1-login-tests/headless-01-verify.json
```

Both browser lifetimes must validate their API samples. On `franz`, first update
the isolated source to the reviewed commit, run the 99 offline tests, then repeat
the fresh headless login/verify procedure with its own private credential file.
Never copy the Mac profile to Linux. Record the actual runtime, initial login
time and any manual assistance separately from the existing longevity series.

## Where cookies/settings live and how to reset

The default profile is `<checkout>/.m1-state/profile`. Explicit state paths put
all browser settings, cookies and local storage in `<state-dir>/profile`.
For the GUI example: `.m1-login-tests/gui-01/.m1-state/profile/`.

Prefer a new run name over deleting anything. To reset only the disposable GUI
profile, stop its M1/Chromium processes first, confirm the path, then from the
checkout root run this interactive removal:

```sh
rm -ri -- "$PWD/.m1-login-tests/gui-01/.m1-state/profile"
```

It asks before removal. This deletes that test browser's session/settings, while
preserving its marker, logs and saved summaries. The next command recreates an
empty profile. Do not delete the default longevity profile, lock files of a running
browser, or the old application's directory. A fresh profile may cause another
password submission, so don't repeatedly rerun failed passwords without diagnosis.

Before staging, undo with the reverse patch after `--reverse --check`. After a
commit, use `git revert <commit-id>` and rerun tests. Preserve private credentials,
profiles and logs deliberately; do not use `git clean -fdx` or `reset --hard`.
