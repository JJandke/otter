"""Finite live acceptance runs for macOS/Linux; never production scheduling.

Run explicitly with a private credential file. No live test runs on import or in
unit discovery. Each application invocation is a new process/browser lifetime.
"""

import argparse
import hashlib
import json
import math
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .auth_flow import UI_ERROR_KINDS, UI_OPERATIONS
from .credential_step import OUTCOMES
from .errors import ConfigurationError
from .state import MARKER, OLD_APPLICATION, _has_symlink, _overlap, private_state

PROJECT = Path(__file__).resolve().parents[2]
STATES = {"starting", "redirect", "student", "portal_login", "b2c_microsoft", "b2c_student",
          "email", "password", "password_choice", "kmsi", "study", "schedule", "timetable",
          "native_passkey", "unknown"}
ACTIONS = STATES - {"starting", "redirect", "native_passkey", "timetable", "unknown"}
CATEGORIES = {"authentication", "bootstrap", "configuration", "network", "service",
              "invalid_response", "suspicious_response", "interrupted", "validated_snapshot"}
REASONS = {"session_requires_interactive_login", "microsoft_rejected_credentials",
           "api_access_denied", "profile_already_in_use", "browser_launch_failed",
           "authentication_ui_operation_failed", "authentication_wait_failed",
           "credentials_file_unreadable_or_invalid", "invalid_credentials_file",
           "credentials_file_must_be_owner_only_regular_file", "api_temporarily_unavailable", "unexpected_authentication_origin",
           "authentication_control_origin_changed", "authentication_form_changed",
           "session_reauthentication_failed", "count_mismatch", "malformed_json",
           "expected_json_content_type", "empty_timetable",
           "credential_control_recovery_timeout", "credential_control_recovery_exhausted",
           "credential_observer_not_installed", "credential_control_verification_failed",
           "credential_form_action_untrusted", "ambiguous_credential_field",
           "ambiguous_credential_submit", "credential_form_repeated_email",
           "credential_form_repeated_password"}
REASONS |= {prefix + state for prefix in (
    "authentication_timeout_", "authentication_state_timeout_", "authentication_transition_limit_"
) for state in STATES}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted([*PROJECT.joinpath("src").rglob("*.py"), PROJECT / "pyproject.toml",
                        PROJECT / "uv.lock"]):
        digest.update(path.relative_to(PROJECT).as_posix().encode())
        digest.update(b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def environment() -> dict:
    try:
        pw_version = version("playwright")
    except PackageNotFoundError:
        pw_version = "not_installed"
    commit, dirty = "unknown", None
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT, capture_output=True,
                                timeout=5, check=False)
        candidate = result.stdout.decode("ascii", errors="ignore").strip()
        if result.returncode == 0 and re.fullmatch(r"[a-f0-9]{40,64}", candidate):
            commit = candidate
        status = subprocess.run(["git", "status", "--porcelain"], cwd=PROJECT,
                                capture_output=True, timeout=5, check=False)
        if status.returncode == 0:
            dirty = bool(status.stdout)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"os": platform.system(), "architecture": platform.machine(),
            "python": platform.python_version(), "playwright": pw_version,
            "git_commit": commit, "working_tree_dirty": dirty,
            "declared_python_match": sys.version_info[:3] == (3, 12, 14),
            "source_sha256": source_fingerprint()}


def create_run_directory(parent: Path) -> Path:
    parent = parent.expanduser().absolute()
    if (parent.name != ".m1-acceptance" or _has_symlink(parent) or ".git" in parent.parts
            or _overlap(parent.resolve(), OLD_APPLICATION)):
        raise ConfigurationError("unsafe_acceptance_directory")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = parent.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ConfigurationError("acceptance_directory_must_be_owned_and_private")
    marker = parent / ".owner"
    if not marker.exists():
        if any(parent.iterdir()):
            raise ConfigurationError("unrecognised_acceptance_directory")
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(MARKER)
    if marker.is_symlink() or marker.read_text() != MARKER:
        raise ConfigurationError("invalid_acceptance_marker")
    return Path(tempfile.mkdtemp(prefix="run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"),
                                 dir=parent))


def submission_evidence(diag: dict, actions: dict) -> dict:
    """Attempt counters cannot prove a POST; old/missing evidence stays unknown."""
    counts = diag.get("credential_submissions_observed")
    outcomes = diag.get("credential_submission_outcomes")
    present = isinstance(counts, dict) and isinstance(outcomes, dict)
    if counts is None and outcomes is None:
        counts, outcomes = {}, {}
    elif (not present or set(counts) != set(outcomes)
          or any(k not in {"email", "password"} or type(v) is not int or v not in (0, 1)
                 for k, v in counts.items())
          or any(not isinstance(v, str) or v not in OUTCOMES for v in outcomes.values())
          or any(bool(counts[k]) != (outcomes[k] == "request_observed") for k in counts)):
        raise ValueError("invalid_credential_submission_evidence")
    attempted = {k for k in ("email", "password") if actions.get(k, 0)}
    if set(counts) - attempted:
        raise ValueError("submission_without_attempt")
    complete = present and attempted <= set(counts) and all(
        outcomes[k] == "request_observed" for k in attempted)
    recoveries = diag.get("credential_control_recoveries", {})
    if (not isinstance(recoveries, dict)
            or any(k not in {"email", "password"} or type(v) is not int or not 0 <= v <= 3
                   for k, v in recoveries.items())):
        raise ValueError("invalid_credential_recovery_evidence")
    return {"credential_submissions_observed": counts,
            "credential_submission_outcomes": outcomes,
            "credential_control_recoveries": recoveries,
            "credential_evidence_complete": complete}


def project_summary(payload: object, command: str, exit_code: int) -> dict:
    """Copy only validated fixed fields, never arbitrary child text or stderr."""
    if (not isinstance(payload, dict) or type(payload.get("ok")) is not bool
            or payload.get("command") != command or payload["ok"] != (exit_code == 0)
            or payload.get("category") not in CATEGORIES
            or (not payload["ok"] and payload["category"] == "validated_snapshot")):
        raise ValueError("invalid_child_summary")
    result = {"ok": payload["ok"], "category": payload["category"], "command": command,
              "exit_code": exit_code}
    if not payload["ok"]:
        reason = payload.get("reason")
        result["reason"] = reason if isinstance(reason, str) and reason in REASONS else "unclassified_child_failure"
    diagnostics = payload.get("diagnostics", {})
    if isinstance(diagnostics, dict):
        if diagnostics.get("auth_state") in STATES:
            result["auth_state"] = diagnostics["auth_state"]
        if diagnostics.get("auth_operation") in UI_OPERATIONS:
            result["auth_operation"] = diagnostics["auth_operation"]
        if diagnostics.get("last_ui_error_kind") in UI_ERROR_KINDS:
            result["last_ui_error_kind"] = diagnostics["last_ui_error_kind"]
    runs = payload.get("browser_runs", [])
    if not isinstance(runs, list) or len(runs) > 2:
        raise ValueError("invalid_browser_runs")
    projected = []
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("invalid_browser_run")
        counts, samples = run.get("event_counts"), run.get("direct_api_samples")
        if (not isinstance(counts, list) or not counts or len(counts) > 1800
                or any(type(n) is not int or n <= 0 for n in counts)
                or type(samples) is not int or samples != len(counts)):
            raise ValueError("invalid_sample_evidence")
        diag = run.get("diagnostics", {})
        if (not isinstance(diag, dict) or diag.get("browser_mode") != "headless"
                or not isinstance(diag.get("auth_actions"), dict)):
            raise ValueError("invalid_diagnostics")
        actions = diag.get("auth_actions", {})
        if any(key not in ACTIONS or type(count) is not int or not 0 <= count <= 2
               for key, count in actions.items()):
            raise ValueError("invalid_action_evidence")
        projected.append({"event_counts": counts, "direct_api_samples": samples,
                          "auth_actions": actions, **submission_evidence(diag, actions)})
    if payload["ok"]:
        expected_runs, minimum_samples = (2, 2) if command == "verify" else (1, 1)
        if (payload["category"] != "validated_snapshot" or len(projected) != expected_runs
                or any(run["direct_api_samples"] < minimum_samples for run in projected)):
            raise ValueError("missing_validation_evidence")
    result["browser_runs"] = projected
    action_sets = [run["auth_actions"] for run in projected]
    failed_actions = diagnostics.get("auth_actions") if isinstance(diagnostics, dict) else None
    if not payload["ok"] and isinstance(failed_actions, dict):
        if any(key not in ACTIONS or type(count) is not int or not 0 <= count <= 2
               for key, count in failed_actions.items()):
            raise ValueError("invalid_failed_action_evidence")
        result["failed_auth_actions"] = failed_actions
        action_sets.append(failed_actions)
    evidence = list(projected)
    if not payload["ok"] and isinstance(failed_actions, dict):
        failed_evidence = submission_evidence(diagnostics, failed_actions)
        result.update(failed_evidence)
        evidence.append(failed_evidence)
    result["credential_evidence_complete"] = bool(evidence) and all(
        row["credential_evidence_complete"] for row in evidence)
    result["credential_attempted"] = any(
        actions.get("email", 0) or actions.get("password", 0) for actions in action_sets)
    result["credential_submission_used"] = any(
        any(row["credential_submissions_observed"].values()) for row in evidence)
    result["password_submission_observed"] = any(
        row["credential_submissions_observed"].get("password", 0) for row in evidence)
    return result


def stop_process_group(process: subprocess.Popen) -> None:
    """Only target the private process group created for this test invocation."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        if process.stdout is not None:
            process.stdout.close()
        process.wait(timeout=5)


def run_child(argv: list[str], timeout: float) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT / "src")
    process = subprocess.Popen(argv, cwd=PROJECT, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_process_group(process)
        return {"ok": False, "category": "runner_timeout", "exit_code": 124}
    except BaseException:
        stop_process_group(process)
        raise
    if not stdout or len(stdout) > 1024 * 1024:
        return {"ok": False, "category": "runner_protocol", "exit_code": process.returncode}
    try:
        payload = json.loads(stdout)
        return project_summary(payload, argv[3], process.returncode)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return {"ok": False, "category": "runner_protocol", "exit_code": process.returncode}


@dataclass
class Configuration:
    mode: str
    credentials_file: Path = field(repr=False)
    output_dir: Path = Path(".m1-acceptance")
    fresh_cycles: int = 1
    wet_runs: int = 3
    interval_seconds: float = 3600
    duration_hours: float = 26
    idle_hours: float = 25
    bootstrap_seconds: int = 120
    decline_passkey: bool = False


class AcceptanceFailure(Exception):
    pass


class Runner:
    def __init__(self, config: Configuration, directory: Path, *, child=run_child,
                 monotonic=time.monotonic, wall=time.time, sleep=time.sleep):
        self.config, self.directory, self.child = config, directory, child
        self.monotonic, self.wall, self.sleep = monotonic, wall, sleep
        self.metadata = environment()
        self.report = {"schema": 2, "mode": config.mode, "status": "running",
                       "started_at": utc_now(), "environment": self.metadata,
                       "steps": [], "timing": [], "schedule_gaps": 0,
                       "plan": {key: getattr(config, key) for key in (
                           "fresh_cycles", "wet_runs", "interval_seconds", "duration_hours",
                           "idle_hours", "bootstrap_seconds", "decline_passkey")}}
        self.write()

    def write(self) -> None:
        fd, name = tempfile.mkstemp(prefix=".report-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(self.report, file, indent=2, allow_nan=False)
                file.write("\n")
            os.replace(name, self.directory / "report.json")
        finally:
            Path(name).unlink(missing_ok=True)

    def step(self, label: str, state: Path, command: str, *, credentials=False,
             require_password=False) -> dict:
        if source_fingerprint() != self.metadata["source_sha256"]:
            raise AcceptanceFailure("source_changed_during_run")
        argv = [sys.executable, "-m", "provadis_m1", command, "--state-dir", str(state),
                "--bootstrap-seconds", str(self.config.bootstrap_seconds), "--navigation-log"]
        if credentials:
            argv += ["--credentials-file", str(self.config.credentials_file)]
            if command == "login":
                argv.append("--headless")
            if self.config.decline_passkey:
                argv.append("--decline-passkey")
        lifetimes = 2 if command == "verify" else 1
        timeout = lifetimes * (2 * self.config.bootstrap_seconds + 30 * lifetimes + 30) + 10
        started = self.monotonic()
        row = {"label": label, "state": str(state.relative_to(self.directory)),
               "started_at": utc_now(), "credentials_authorized": credentials}
        row["status"] = "running"
        self.report["steps"].append(row)
        self.write()
        result = self.child(argv, timeout)
        row.update(result)
        row["status"] = "passed" if result["ok"] else "failed"
        row["duration_seconds"] = round(self.monotonic() - started, 3)
        self.write()
        if source_fingerprint() != self.metadata["source_sha256"]:
            raise AcceptanceFailure("source_changed_during_run")
        if not result["ok"]:
            raise AcceptanceFailure("child_failed")
        if not credentials and result["credential_attempted"]:
            raise AcceptanceFailure("unexpected_credential_submission")
        if require_password and not result["password_submission_observed"]:
            self.report["status"] = "inconclusive"
            raise AcceptanceFailure("fresh_credential_submission_not_demonstrated")
        return result

    def state(self, name: str) -> Path:
        # Run directories are newly created; existing profiles are never selected/deleted.
        return self.directory / name / ".m1-state"

    def wait(self, state: Path, seconds: float) -> dict:
        start_mono, start_wall = self.monotonic(), self.wall()
        target = start_mono + seconds
        self.report["waiting"] = {
            "state": str(state.relative_to(self.directory)), "requested_seconds": seconds,
            "started_at": utc_now(),
            "not_before_utc": datetime.fromtimestamp(start_wall + seconds, timezone.utc).isoformat(),
        }
        self.write()
        # Block other OTTER commands from using this dedicated profile while idle.
        with private_state(state):
            while self.monotonic() < target:
                self.sleep(min(30, target - self.monotonic()))
        elapsed_wall, elapsed_mono = self.wall() - start_wall, self.monotonic() - start_mono
        evidence = {"requested_seconds": seconds, "elapsed_seconds": round(elapsed_wall, 3),
                    "elapsed_monotonic_seconds": round(elapsed_mono, 3)}
        self.report.pop("waiting", None)
        self.report["timing"].append(evidence)
        self.write()
        if elapsed_wall < seconds - 1:
            raise AcceptanceFailure("clock_moved_backwards")
        return evidence

    def execute(self) -> int:
        try:
            if self.config.mode == "smoke":
                for cycle in range(1, self.config.fresh_cycles + 1):
                    state = self.state(f"login-{cycle:03d}")
                    self.step("fresh_login", state, "login", credentials=True, require_password=True)
                    for _ in range(self.config.wet_runs):
                        self.step("wet_fetch", state, "fetch")
                    self.step("strict_restart_verify", state, "verify")
                    cold = self.state(f"cold-fetch-{cycle:03d}")
                    self.step("automatic_cold_fetch", cold, "fetch", credentials=True,
                              require_password=True)
                    self.step("cold_fetch_restart_verify", cold, "verify")
            else:
                state = self.state(self.config.mode)
                self.step("automatic_baseline", state, "fetch", credentials=True, require_password=True)
                self.step("strict_restart_verify", state, "verify")
                if self.config.mode == "idle":
                    self.wait(state, self.config.idle_hours * 3600)
                    result = self.step("after_idle", state, "fetch", credentials=True)
                    self.report["idle_reuse_without_credentials"] = (
                        not result["credential_attempted"] and result["credential_evidence_complete"])
                else:
                    # Start-to-start samples; skip missed slots rather than issuing catch-up bursts.
                    start, wall_start = self.monotonic(), self.wall()
                    horizon = self.config.duration_hours * 3600
                    due, previous_wall = min(self.config.interval_seconds, horizon), wall_start
                    while True:
                        self.wait(state, max(0, start + due - self.monotonic()))
                        current_wall = self.wall()
                        if current_wall - previous_wall > self.config.interval_seconds + max(5, self.config.interval_seconds * .2):
                            self.report["schedule_gaps"] += 1
                        previous_wall = current_wall
                        probe_elapsed = self.monotonic() - start
                        self.step("active_fetch", state, "fetch", credentials=True)
                        elapsed = self.monotonic() - start
                        if probe_elapsed >= horizon:
                            break
                        due = min(horizon, (math.floor(elapsed / self.config.interval_seconds) + 1)
                                  * self.config.interval_seconds)
                    self.report["active_elapsed_seconds"] = round(self.wall() - wall_start, 3)
                    if self.report["schedule_gaps"]:
                        self.report["status"] = "inconclusive"
                        raise AcceptanceFailure("active_schedule_had_gaps")
                self.step("final_verify", state, "verify", credentials=True)
            self.report["status"] = "passed"
            code = 0
        except AcceptanceFailure as error:
            if self.report["status"] != "inconclusive":
                self.report["status"] = "failed"
            self.report["reason"] = str(error)  # Only fixed runner-owned codes reach this class.
            code = 5 if self.report["status"] == "inconclusive" else 1
        except KeyboardInterrupt:
            self.report.update(status="interrupted", reason="operator_interrupted")
            code = 130
        except Exception:
            self.report.update(status="failed", reason="runner_operation_failed")
            code = 6
        self.report["finished_at"] = utc_now()
        self.report["credential_attempting_steps"] = sum(
            bool(row.get("credential_attempted")) for row in self.report["steps"])
        self.report["credential_submitting_steps"] = sum(
            bool(row.get("credential_submission_used")) for row in self.report["steps"])
        self.write()
        return code


def bounded_number(low: float, high: float, *, integer=False):
    def parse(value: str):
        try:
            number = int(value) if integer else float(value)
        except ValueError:
            raise argparse.ArgumentTypeError("invalid numeric value") from None
        if not math.isfinite(number) or not low <= number <= high:
            raise argparse.ArgumentTypeError(f"value must be between {low} and {high}")
        return number
    return parse


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="OTTER finite live M1 acceptance tests")
    cli.add_argument("mode", choices=("smoke", "active", "idle"))
    cli.add_argument("--credentials-file", required=True, type=Path)
    cli.add_argument("--output-dir", type=Path, default=Path(".m1-acceptance"))
    cli.add_argument("--fresh-cycles", type=bounded_number(1, 5, integer=True), default=1)
    cli.add_argument("--wet-runs", type=bounded_number(1, 20, integer=True), default=3)
    cli.add_argument("--interval-seconds", type=bounded_number(1, 86400), default=3600)
    cli.add_argument("--duration-hours", type=bounded_number(.001, 720), default=26)
    cli.add_argument("--idle-hours", type=bounded_number(.001, 720), default=25)
    cli.add_argument("--bootstrap-seconds", type=bounded_number(1, 1800, integer=True), default=120)
    cli.add_argument("--decline-passkey", action="store_true")
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    os.umask(0o077)
    try:
        if any(os.environ.get(key) for key in ("DEBUG", "PWDEBUG", "SSLKEYLOGFILE")):
            raise ConfigurationError("disable_browser_debug_and_tls_key_logging")
        config = Configuration(**vars(args))
        config.credentials_file = config.credentials_file.expanduser().absolute()
        directory = create_run_directory(config.output_dir)
        runner = Runner(config, directory)
        code = runner.execute()
        print(json.dumps({"ok": code == 0, "status": runner.report["status"],
                          "report": str(directory / "report.json")}))
        return code
    except ConfigurationError as error:
        print(json.dumps({"ok": False, "category": "configuration", "reason": error.reason}))
        return 6
    except Exception:
        print(json.dumps({"ok": False, "category": "configuration", "reason": "runner_setup_failed"}))
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
