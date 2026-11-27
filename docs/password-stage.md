# M1: retained email control on the password page

The gui-04 log shows the password option was clicked, then both email and password
controls matched. No password attempt occurred. This corrects the overly strict
requirement that the email control disappear: after the confirmed password-option
click, permit the unique password field even when email is retained. Before that
click the transition guard remains. Exact-origin checks, bound-element validation,
unique-control checks and the one-attempt limit are unchanged. No live success
is claimed; this patch addresses the specific state in the owner's log.

Continue the existing issue branch. The assistant changed only an isolated source
copy, without reading real credentials or modifying profiles. Keep the previous
FIDO bridge and password-choice patches installed.

On the Mac, from the repository root:

```sh
git apply --check ~/Downloads/OTTER_M1_Password_Stage.patch
git apply ~/Downloads/OTTER_M1_Password_Stage.patch
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
git diff --check
```

Expect **102 tests, OK**. The new regression reproduces a confirmed password choice
with both controls retained, then verifies one password submission and no email
refill. Existing tests retain the wait during the initial email transition.
PyCharm can run the same command in its terminal, or Unittests targeting `tests`
with project-root working directory, current interpreter and `PYTHONPATH=src`.
Ubuntu uses the same offline command in its isolated checkout.

Repeat the GUI test with fresh `gui-05` state and matching summary filename,
keeping the credential file outside Git and `--decline-passkey`. Watch without
interacting first. Preserve the safe summary and JSONL if it fails. Keep the
original longevity profile intact. Once GUI login works automatically, test a
fresh headless profile and then Ubuntu; reused manual-login state cannot prove
initial credential automation.

Review and stage only:

```sh
git add src/provadis_m1/password_login.py tests/test_password_login.py docs/password-stage.md
.venv/bin/python scripts/check_public_tree.py
git diff --cached --check
git diff --cached
```

After review: `fix(auth): allow retained email controls after password selection`,
with `Refs #N` in the body for the actual issue number. Check status/remote before
any later push. No server deployment or production configuration changes occur.
Before staging, reverse the patch after `--reverse --check`; after committing,
use `git revert <commit-id>` and rerun tests. Preserve credentials and profiles.
