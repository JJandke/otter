# M1: Microsoft FIDO bridge correction

The screenshot shows `login.microsoft.com/common/bridge/fido`, not the
`login.microsoftonline.com` hostname used by the password form. The earlier
refusal script did not cover the bridge. This patch enables opt-in WebAuthn
refusal on that exact HTTPS origin/path only, and labels its navigation safely.
Queries remain redacted. Password filling remains restricted to the original
Microsoft login origin. It now waits until the email control disappears before
attempting the password stage; both preloaded fields no longer trigger an early
password attempt during the redirect.

This is a proposed correction based on observed routing, not live proof that
Microsoft's fallback completes. A required MFA factor is not bypassed. No real
credential file or authenticated profile was read by the assistant.

Based on `72ffd0f`; continue `fix/m1-session-diagnostics`. On the Mac:

```sh
git apply --check ~/Downloads/OTTER_M1_Fido_Bridge.patch
git apply ~/Downloads/OTTER_M1_Fido_Bridge.patch
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
git diff --check
```

Expect 99 tests and OK. The updated regression checks that password filling waits
for the email form to disappear and never targets the bridge. Separate synthetic
JavaScript checks covered the bridge, original origin, unrelated paths/hosts and
non-WebAuthn passthrough. These do not establish actual fallback behavior.
PyCharm uses the same terminal command, or Unittests targeting `tests` with
project-root working directory, `PYTHONPATH=src` and the current interpreter.
Ubuntu uses the same offline checks in its isolated checkout.

Repeat the GUI command from `login-form-tests.md` with a NEW `gui-02` profile
and summary filename, keeping `--decline-passkey`. Observe without clicking first.
The summary and JSONL locations are unchanged. Preserve any failure and describe
the last visible page without sharing credential values or query parameters.
Do not reuse/delete the default longevity-test profile. Then test a fresh headless
profile locally and finally on the server if the GUI run succeeds automatically.

Review and stage only:

```sh
git add src/provadis_m1/passkey.py src/provadis_m1/password_login.py \
  src/provadis_m1/navigation_log.py src/provadis_m1/browser.py \
  tests/test_password_login.py docs/fido-bridge.md
.venv/bin/python scripts/check_public_tree.py
git diff --cached --check
git diff --cached
```

After review: `fix(auth): decline passkeys on the Microsoft FIDO bridge`, with
`Refs #N` in the body for the actual issue number. Check status/remote and reviewed
commit before any push. No deployment or server configuration change is included.
Before staging, reverse the patch after `--reverse --check`; after committing use
`git revert <commit-id>` and rerun tests. Preserve private profiles and credentials.
