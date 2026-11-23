# OTTER — Organised Timetable Translation and Event Retrieval

## Scope and safety
- Current scope is M1: foundation and authentication feasibility. Do not implement
  later calendar milestones without a new request.
- Preserve `/home/ubuntu/provadis_timetable`. Do not change production Nginx or
  systemd configuration or deploy without explicit approval.
- Never request or expose passwords, cookies, tokens, OAuth codes, or MFA codes.
- Keep browser profiles and raw personal responses out of Git and deliverables.

## Owner workflow and learning requirements
The owner knows Python and IT administration and is learning Git and automated tests.
For every milestone or implementation increment:
- Explain the Git workflow and why it fits; avoid unnecessary complexity.
- Prefer meaningful Conventional Commit messages and explain their purpose.
- Distinguish actions performed automatically from commands the owner must run.
- Document relevant terminal and PyCharm tests, plus Ubuntu steps where applicable.
- Explain what tests verify, what passing/failing means, and what remains unproven.
- Provide verification before committing, pushing, or deploying.
- Explain recovery/rollback without discarding user work or authentication state.
- Teach concepts when they become relevant, using practical steps.
