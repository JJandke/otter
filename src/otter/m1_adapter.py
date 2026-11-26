"""One private candidate from the existing M1 browser/fetch pipeline."""

import os
import stat
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from provadis_m1.browser import browser_run
from provadis_m1.credentials import load_credentials
from provadis_m1.errors import ConfigurationError
from provadis_m1.navigation_log import NavigationLog
from provadis_m1.state import OLD_APPLICATION, private_state
from provadis_m1.validation import parse_candidate_response

from .ingestion import CapturedSnapshot
from .models import SnapshotCandidate


@dataclass(frozen=True, slots=True)
class FetchOptions:
    state_dir: Path = field(default=Path(".otter-runtime/.m1-state"), repr=False)
    credentials_file: Path | None = field(default=None, repr=False)
    headed: bool = False
    decline_passkey: bool = False
    navigation_log: bool = False
    bootstrap_seconds: int = 60
    request_seconds: int = 30
    state_seconds: int = 60

    def validate(self) -> None:
        if any(os.environ.get(key) for key in ("DEBUG", "PWDEBUG", "SSLKEYLOGFILE")):
            raise ConfigurationError("disable_browser_debug_and_tls_key_logging")
        if any(type(value) is not int or not 1 <= value <= 1800 for value in
               (self.bootstrap_seconds, self.request_seconds, self.state_seconds)):
            raise ConfigurationError("invalid_fetch_budget")
        if any(type(value) is not bool for value in (self.headed, self.decline_passkey, self.navigation_log)):
            raise ConfigurationError("invalid_fetch_options")
        if self.decline_passkey and self.credentials_file is None:
            raise ConfigurationError("passkey_decline_requires_credentials")


class M1Provider:
    def __init__(self, options: FetchOptions, *, database_directory: Path,
                 clock: Callable[[], datetime] | None = None):
        options.validate()
        state = options.state_dir.expanduser().absolute()
        data = database_directory.expanduser().absolute().resolve()
        if state.name not in {".m1-state", ".provadis-m1"}:
            raise ConfigurationError("state_directory_must_have_dedicated_name")
        if (any(p.is_symlink() for p in (state, *state.parents))
                or {".git", ".m1-acceptance", ".m1-login-tests"}.intersection(state.parts)
                or state == OLD_APPLICATION or OLD_APPLICATION in state.parents or state in OLD_APPLICATION.parents):
            raise ConfigurationError("unsafe_state_directory")
        resolved = state.resolve()
        if resolved == data or resolved in data.parents or data in resolved.parents:
            raise ConfigurationError("database_and_profile_must_be_separate")
        self.options, self.state = options, state
        self.clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        self.evidence: dict = {}

    def __call__(self) -> CapturedSnapshot:
        self.options.validate()
        self.evidence = {}
        parent = self.state.parent
        if not parent.exists():
            parent.mkdir(mode=0o700)
        info = parent.stat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022):
            raise ConfigurationError("unsafe_state_parent")
        credentials = (load_credentials(self.options.credentials_file)
                       if self.options.credentials_file is not None else None)
        captured = []

        def receive(snapshot):
            if not isinstance(snapshot, SnapshotCandidate) or captured:
                raise ConfigurationError("candidate_handoff_invalid")
            captured.append(CapturedSnapshot(snapshot, self.clock()))

        with private_state(self.state) as profile, ExitStack() as stack:
            trace = (stack.enter_context(NavigationLog(profile.parent))
                     if self.options.navigation_log else None)
            summary = browser_run(
                profile, interactive=False, headed=self.options.headed,
                bootstrap_seconds=self.options.bootstrap_seconds,
                request_seconds=self.options.request_seconds, state_seconds=self.options.state_seconds,
                samples=1, sample_interval=0, credentials=credentials,
                decline_passkey=self.options.decline_passkey, trace=trace,
                snapshot_validator=parse_candidate_response, on_snapshot=receive,
            )
        # Browser cleanup and profile lock release precede database finalization.
        counts = summary.get("event_counts")
        if (len(captured) != 1 or type(summary.get("direct_api_samples")) is not int
                or summary["direct_api_samples"] != 1
                or not isinstance(counts, list) or len(counts) != 1 or type(counts[0]) is not int
                or counts != [captured[0].candidate.event_count]):
            raise ConfigurationError("candidate_handoff_invalid")
        self.evidence = {"direct_api_samples": 1, "event_count": captured[0].candidate.event_count}
        diagnostics = summary.get("diagnostics", {})
        if isinstance(diagnostics, dict):
            for key in ("direct_api_attempts", "direct_api_transport_failures", "page_error_count"):
                value = diagnostics.get(key)
                if type(value) is int and value >= 0:
                    self.evidence[key] = value
            for source, target in (("auth_actions", "credential_attempted"),
                                   ("credential_submissions_observed", "credential_submission_observed")):
                actions = diagnostics.get(source)
                if isinstance(actions, dict) and all(type(actions.get(k, 0)) is int and actions.get(k, 0) >= 0
                                                      for k in ("email", "password")):
                    self.evidence[target] = any(actions.get(k, 0) > 0 for k in ("email", "password"))
        return captured[0]
