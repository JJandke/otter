"""M2 CLI: one-shot ingestion and maintenance; stdout is redacted JSON."""

import argparse
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

from provadis_m1.errors import ConfigurationError, InvalidResponse, M1Error
from provadis_m1.validation import MAX_RESPONSE_BYTES, parse_candidate_response

from ._store_files import StorageError
from .assessment import AssessmentPolicy
from .backups import backup_store, check_store, restore_backup, verify_backup
from .ingestion import (
    FAILURE_REASONS, IngestionError, IngestionService, ObservationResult, PendingObservationError, REVIEW_REASONS,
)
from .m1_adapter import FetchOptions, M1Provider
from .retention import prune_quarantine
from .storage import Store, initialize_store, migrate_store

CONFIG_REASONS = frozenset({
    "invalid_arguments", "invalid_assessment_policy", "invalid_review_arguments", "approval_flag_required",
    "disable_browser_debug_and_tls_key_logging", "invalid_fetch_budget", "invalid_fetch_options",
    "passkey_decline_requires_credentials", "state_directory_must_have_dedicated_name",
    "unsafe_state_directory", "database_and_profile_must_be_separate", "unsafe_state_parent",
})
STORAGE_REASONS = frozenset({
    "storage_busy", "storage_constraint_failed", "storage_sql_failed", "storage_io_failed",
    "unsafe_store_file", "unsafe_store_directory", "store_directory_must_be_owned_and_private",
    "unrecognized_store_directory", "invalid_store_marker", "store_already_in_use",
    "unsupported_store_configuration", "store_integrity_failed", "migration_backup_timeout",
    "store_already_exists", "uninitialized_store_schema", "store_is_closed", "nested_store_transaction",
    "store_write_requires_transaction", "invalid_attempt_context", "unsupported_candidate_version",
    "invalid_normalized_content", "content_digest_conflict", "content_not_found", "unsupported_content_version",
    "content_integrity_failed", "invalid_store_state", "invalid_accepted_pointer", "invalid_migration_sequence",
    "content_expired", "invalid_content_expiry", "backup_schema_mismatch", "invalid_backup_file",
    "backup_has_sidecars", "backup_timeout", "backup_content_mismatch", "unsafe_backup_directory",
    "backup_observation_mismatch",
    "restore_destination_not_empty", "invalid_retention_policy",
    "unrecognized_store_schema", "invalid_migration_history", "schema_newer_than_application",
    "schema_checksum_mismatch", "schema_migration_required", "storage_time_must_be_aware",
})
INGESTION_REASONS = frozenset({
    "invalid_observation_time", "invalid_observation_uuid", "invalid_observation_origin",
    "invalid_captured_observation", "capture_requires_snapshot_candidate", "invalid_assessment_record",
    "invalid_ingestion_policy", "observation_not_found", "observation_id_conflict",
    "unsupported_observation_policy", "observation_is_not_latest", "recorded_observation_has_no_receipt",
    "observation_processing_failed", "invalid_review_context", "review_baseline_changed", "review_id_conflict",
    "observation_not_reviewable", "review_requires_fresh_observation", "review_requires_new_observation",
})


def _fixed(value, allowed: frozenset, fallback: str) -> str:
    return value if isinstance(value, str) and value in allowed else fallback


class SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        # argparse normally echoes invalid arguments, which may contain secrets.
        raise ConfigurationError("invalid_arguments")


def parser() -> argparse.ArgumentParser:
    cli = SafeParser(description="OTTER M2 validation and persistence (no calendar/deployment)")
    commands = cli.add_subparsers(dest="command", required=True, parser_class=SafeParser)
    for name in ("init", "migrate", "status", "schema", "fetch", "import-json", "observation", "review", "recover",
                 "backup", "verify-backup", "restore", "check", "prune-quarantine"):
        command = commands.add_parser(name)
        command.add_argument("--directory", type=Path, default=Path(".otter-data"))
        if name in {"fetch", "import-json"}:
            command.add_argument("--minimum-future-drop", type=int, default=5)
            command.add_argument("--future-drop-percent", type=int, default=30)
        if name == "fetch":
            command.add_argument("--state-dir", type=Path, default=Path(".otter-runtime/.m1-state"))
            command.add_argument("--credentials-file", type=Path)
            command.add_argument("--headed", action="store_true")
            command.add_argument("--decline-passkey", action="store_true")
            command.add_argument("--navigation-log", action="store_true")
            command.add_argument("--bootstrap-seconds", type=int, default=60)
            command.add_argument("--request-seconds", type=int, default=30)
            command.add_argument("--state-seconds", type=int, default=60)
        elif name == "import-json":
            command.add_argument("file", type=Path)
            command.add_argument("--observation-id", required=True)
        elif name == "backup":
            command.add_argument("--backup-directory", type=Path)
        elif name in {"verify-backup", "restore"}:
            command.add_argument("file", type=Path)
        elif name == "prune-quarantine":
            command.add_argument("--older-than-days", type=int, default=30)
            command.add_argument("--apply", action="store_true")
        elif name in {"observation", "review"}:
            command.add_argument("observation_id")
            if name == "review":
                command.add_argument("--approve", action="store_true")
                command.add_argument("--expected-baseline", help="Positive attempt number or 'none'")
                command.add_argument("--reason", choices=tuple(REVIEW_REASONS))
    return cli


def _policy(args) -> AssessmentPolicy:
    try:
        return AssessmentPolicy(args.minimum_future_drop, args.future_drop_percent)
    except ValueError:
        raise ConfigurationError("invalid_assessment_policy") from None


def _file_candidate(path: Path):
    try:
        fd = os.open(path.expanduser(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ConfigurationError("input_file_unavailable")
            if info.st_size > MAX_RESPONSE_BYTES:
                raise InvalidResponse("invalid_response_size")
            body = file.read(MAX_RESPONSE_BYTES + 1)
    except OSError:
        raise ConfigurationError("input_file_unavailable") from None
    return parse_candidate_response(200, "application/json", body)


def _outcome_code(result: ObservationResult) -> int:
    if result.status == "accepted":
        return 0
    if result.status == "suspicious":
        return 5
    if result.status == "invalid":
        return 4
    if result.status == "interrupted":
        return 130
    return {"authentication": 2, "network": 3, "configuration": 6, "service": 7,
            "bootstrap": 8, "storage": 9}.get(result.category, 6)


def _baseline(value: str) -> int | None:
    if value == "none":
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ConfigurationError("invalid_review_arguments") from None
    if result < 1:
        raise ConfigurationError("invalid_review_arguments")
    return result


def _preview(store: Store, service: IngestionService, observation_id: str) -> dict:
    result = service.result(observation_id)
    row = store.attempt(observation_id)
    return {"observation": result.safe_summary(), "baseline_attempt_no": row["baseline_attempt_no"],
            "content_expired": store.content_expired(row["content_id"]) if row["content_id"] is not None else False,
            "event_count": row["reported_count"], "received_at_utc_us": row["received_at_utc_us"],
            "is_latest_observation": row["attempt_no"] == store.latest_attempt_no(),
            "policy_version": row["policy_version"], "minimum_future_drop": row["minimum_future_drop"],
            "future_drop_percent": row["future_drop_percent"]}


def _execute(args) -> tuple[dict, int]:
    options = None
    policy = _policy(args) if args.command in {"fetch", "import-json"} else AssessmentPolicy()
    if args.command == "fetch":
        options = FetchOptions(args.state_dir, args.credentials_file, args.headed, args.decline_passkey,
                               args.navigation_log, args.bootstrap_seconds, args.request_seconds, args.state_seconds)
        options.validate()
    if args.command == "review":
        if args.approve:
            if args.expected_baseline is None or args.reason is None:
                raise ConfigurationError("invalid_review_arguments")
            expected = _baseline(args.expected_baseline)
        elif args.expected_baseline is not None or args.reason is not None:
            raise ConfigurationError("approval_flag_required")
    if args.command == "init":
        initialize_store(args.directory)
    elif args.command == "migrate":
        migrate_store(args.directory)
    elif args.command == "backup":
        return backup_store(args.directory, backup_directory=args.backup_directory), 0
    elif args.command == "verify-backup":
        return verify_backup(args.file), 0
    elif args.command == "restore":
        return restore_backup(args.file, args.directory), 0
    elif args.command == "check":
        return check_store(args.directory), 0
    elif args.command == "prune-quarantine":
        return prune_quarantine(args.directory, older_than_days=args.older_than_days, apply=args.apply), 0
    with Store(args.directory) as store:
        service = IngestionService(store, policy=policy)
        if args.command == "fetch":
            provider = M1Provider(options, database_directory=store.directory)
            result = service.fetch(provider)
            return {"observation": result.safe_summary(), "fetch": provider.evidence}, _outcome_code(result)
        if args.command == "import-json":
            result = service.import_candidate(args.observation_id, lambda: _file_candidate(args.file))
            return {"observation": result.safe_summary()}, _outcome_code(result)
        if args.command == "review" and args.approve:
            result = service.approve(args.observation_id, expected_baseline_attempt_no=expected, reason=args.reason)
            return {"observation": result.safe_summary()}, _outcome_code(result)
        if args.command in {"review", "observation"}:
            return _preview(store, service, args.observation_id), 0
        if args.command == "recover":
            return {"recovered_attempts": service.recover_interrupted()}, 0
        if args.command == "schema":
            return {"schema": store.schema()}, 0
        return store.status(), 0


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    response = {"ok": False}
    try:
        args = parser().parse_args(argv)
        response["command"] = args.command
        result, code = _execute(args)
        response.update(result=result, ok=code == 0)
    except PendingObservationError as error:
        code = 9
        response.update(category="storage", reason="observation_write_pending",
                        observation_uuid=error.context.observation_uuid)
    except StorageError as error:
        code = 9
        response.update(category="storage", reason=_fixed(error.reason, STORAGE_REASONS, "storage_failed"))
    except IngestionError as error:
        code = 10
        response.update(category="ingestion", reason=_fixed(str(error), INGESTION_REASONS, "ingestion_failed"))
    except M1Error as error:
        codes = {"authentication": 2, "network": 3, "invalid_response": 4, "suspicious_response": 5,
                 "configuration": 6, "service": 7, "bootstrap": 8}
        category = _fixed(error.category, frozenset(codes), "configuration")
        code = codes[category]
        response.update(category=category, reason=_fixed(error.reason, CONFIG_REASONS | FAILURE_REASONS,
                                                       "operation_failed"))
    except KeyboardInterrupt:
        code = 130
        response.update(category="interrupted", reason="operator_interrupted")
    except Exception:
        code = 6
        response.update(category="configuration", reason="unexpected_local_error")
    response["finished_at"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(response, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
