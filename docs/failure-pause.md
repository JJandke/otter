# M1: inspect a failed headed login

The latest gui-06 summary reports a password attempt but no completed password
submission. It cannot establish whether field resolution, origin verification,
filling or clicking failed. This patch reports the fixed operation name and adds
`--failure-pause-seconds 60` for headed diagnosis. It also includes the email-refill
guard missing from the installed version. No raw error text or values are logged.

Continue the issue branch. The assistant edited an isolated copy, not the owner's
checkout/profile. Apply and run from the Mac repository root:

```sh
git apply --check ~/Downloads/OTTER_M1_Failure_Pause.patch
git apply ~/Downloads/OTTER_M1_Failure_Pause.patch
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
git diff --check
```

Expect 104 tests and OK, covering visible-only option validation, forwarding and
mocked failure pause/cleanup, plus existing credential protections. PyCharm uses
the same terminal command or the documented Unittests configuration; Ubuntu uses
the same offline checks. No live login is claimed.

Repeat the gui-07 test from login-form-tests.md with `--headed` and the additional
`--failure-pause-seconds 60`, preserving the existing credential-file and refusal
flags. On failure it waits up to 60 seconds before closing. The summary prints
AFTER the pause; the JSONL flushes immediately and includes `failure_pause`.
Observe the page without entering credentials manually. Closing the window early
retains the original failure result. The option is rejected for headless/verify.
The state lock remains held during inspection; do not launch another run on it.
Use fresh disposable state and keep the default longevity profile unchanged.

Review and stage only the patch paths:

```sh
git add src/provadis_m1/__main__.py src/provadis_m1/browser.py \
  src/provadis_m1/password_login.py src/provadis_m1/navigation_log.py \
  tests/test_cli.py tests/test_browser.py docs/failure-pause.md
.venv/bin/python scripts/check_public_tree.py
git diff --cached --check
git diff --cached
```

Suggested commit: `feat(auth): retain failed headed sessions for inspection`, with
`Refs #N` for the actual issue. Review status/remote before any push. No deployment
or production configuration change occurs. Before staging reverse the patch after
`--reverse --check`; after committing use `git revert <commit-id>` and rerun tests.
Preserve private credentials and profiles; do not clean/reset the checkout.
