# M1: correct the authenticated dashboard label

## Confirmed evidence

The owner's screenshot shows an authenticated dashboard at `/` with a card
labelled **Mein Studium**. The implementation incorrectly searched for the literal
**My Studium**. The failed summary reported zero timetable requests. In the
manually assisted run, clicking the German card exposed the timetable item;
OTTER then clicked `Stundenplan`, observed the API request and validated 80 events.
The log records that automatic timetable click and subsequent direct API success.
The manual run is successful retrieval evidence, not unattended/restart evidence.

This small patch replaces the incorrect label with the confirmed German label.
It keeps exact visible matching, rejects ambiguous matches, and leaves all SSO
routing, callbacks, profiles and logging behavior unchanged. The diagnostic action
name `my_studium_clicked` is retained for compatibility; it now refers to the
correct German UI label. `/` alone cannot distinguish public and authenticated
portal pages; the prior landing-card hypothesis was not established by that URL.

## Owner workflow

Stay on the existing M1 diagnostic branch. This patch is incremental after the
post-SSO-context patch and was prepared against owner commit `503642d`. The
assistant changed only an isolated source copy; applying the patch does not
commit, push, deploy, or reset your browser profile.

After `git apply --check` succeeds, apply the patch and run from the repository
root in macOS Terminal, PyCharm's terminal, or Ubuntu:

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
git diff --check
```

Expect **67 tests and OK**. New regressions reproduce the authenticated dashboard
with only the German study label, expose the timetable item after that click,
verify no full-document navigation, and reject multiple visible study entries.
The tests are offline/mocked and cannot prove actual unattended authentication.
For PyCharm's GUI runner, select Unittests targeting `tests`, `.venv` as interpreter,
project root as working directory, and `PYTHONPATH=src` (or `src` as Sources Root).

Run the live test without any mouse/keyboard interaction:

```sh
umask 077
set -o pipefail
./scripts/m1 fetch --bootstrap-seconds 120 --navigation-log \
  | tee .m1-state/fetch-summary.json
```

If successful, run `./scripts/m1 verify --bootstrap-seconds 120 --navigation-log`.
Expect both headless browser lifetimes to succeed. `my_studium_clicked` should now
be true when the German card was used; logs should show that click preceding
`timetable_link_clicked` and `api_validated`. A live run can legitimately contain
a different event count, so do not make exact equality with 80 a test requirement.
If it fails, send the final safe summary and the named navigation log; preserve
state and do not guess additional selectors. Headed inspection is available with
`fetch --headed`; on Ubuntu use the documented temporary display/SSH tunnel.
No production Nginx/systemd changes belong in this increment.

Before committing, review `git status` and `git diff`. Stage only the six files
from this patch, including this new document; run `scripts/check_public_tree.py`,
`git diff --cached --check`, and inspect `git diff --cached`. Exclude
`scripts/setup.save` and downloaded patches. Suggested Conventional Commit:
`fix(auth): use the German Mein Studium dashboard label`, with `Refs #N` in the
body (replace N with the actual issue number). Do not close the issue until its
live criteria are met. Review the commit and remote before any later push.

Before staging, undo using `git apply --reverse --check <patch>` and
`git apply --reverse <patch>`. After committing, use `git revert <commit-id>` and
rerun tests. Preserve other user work and private profiles. Server compatibility
and long-running renewal acceptance remain open even if the local verify passes.
