# M1: select the observed password sign-in option

The owner's screenshot shows a Microsoft sign-in method picker after passkey
refusal. The exact visible option is `Use my password`. This incremental patch
clicks that option once, only on the existing trusted Microsoft origin, after
email submission and before any password attempt. Ambiguous matches fail closed.
The bound-element origin check still protects the click and credential filling.
It then resumes the existing password/stay-signed-in/timetable flow. No MFA or
permission consent is automated. Live success remains unproven.

Prepared against the owner's tracked working files, including the applied FIDO
bridge correction. Keep that correction installed. Continue the existing issue
branch. The assistant changed an isolated copy only and used synthetic tests;
no credential files or authenticated profiles were accessed.

On the Mac:

```sh
git apply --check ~/Downloads/OTTER_M1_Password_Choice.patch
git apply ~/Downloads/OTTER_M1_Password_Choice.patch
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
git diff --check
```

Expect **101 tests, OK**. New regressions verify the exact password-option click,
its single-attempt behavior, continuation to password submission, and ambiguity
rejection. PyCharm can run the same terminal command or Unittests targeting
`tests` with the current interpreter, project-root working directory and
`PYTHONPATH=src`. Ubuntu uses the same offline tests in its isolated checkout.

Repeat the GUI command in `login-form-tests.md` with a NEW `gui-04` state and
summary filename, keeping `--decline-passkey`. Do not interact initially. A new
profile is essential: a reused manually authenticated profile only demonstrates
session reuse, not automated initial login. The default longevity profile must
remain unchanged. After a fully automatic GUI pass, repeat with a fresh headless
profile, then on the server. Logs record `password_choice`; diagnostics include
`password_choice_clicked`. No entered values or raw exception messages are logged.

Review `git diff`, then stage this increment's paths (plus the preceding FIDO
correction separately if it has not yet been committed):

```sh
git add src/provadis_m1/password_login.py src/provadis_m1/navigation_log.py \
  tests/test_password_login.py docs/password-choice.md
.venv/bin/python scripts/check_public_tree.py
git diff --cached --check
git diff --cached
```

After review, use `fix(auth): select the Microsoft password sign-in option`, with
`Refs #N` in the body for the actual issue. Review status/remote before a later
push; no server deployment or production configuration change is performed.
Before staging, reverse only this patch after `--reverse --check`; after committing,
use `git revert <commit-id>` and rerun tests. Preserve profiles and other edits.
