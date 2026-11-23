"""Run with PYTHONPATH=src python -m provadis_m1; stdout is sanitized JSON only."""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .browser import browser_run, portal_url
from .errors import ConfigurationError, M1Error
from .state import private_state
from .navigation import DEFAULT_PORTAL_PATH


def _positive(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 1800:
        raise argparse.ArgumentTypeError("value must be between 1 and 1800")
    return number


def _interval(value: str) -> int:
    number = int(value)
    if not 0 <= number <= 60:
        raise argparse.ArgumentTypeError("interval must be between 0 and 60 seconds")
    return number


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="OTTER M1 authentication feasibility only")
    cli.add_argument("command", choices=("login", "fetch", "verify"))
    cli.add_argument("--state-dir", type=Path, default=Path(".m1-state"))
    cli.add_argument("--bootstrap-seconds", type=_positive, default=None)
    cli.add_argument("--request-seconds", type=_positive, default=30)
    cli.add_argument("--samples", type=_positive, default=1)
    cli.add_argument("--sample-interval", type=_interval, default=0)
    cli.add_argument("--portal-path", default=DEFAULT_PORTAL_PATH,
                     help="Same-origin timetable route (default: /student/schedule)")
    cli.add_argument("--headed", action="store_true", help="Visible fetch for diagnosis; not verify")
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    os.umask(0o077)
    result = {"ok": False, "command": args.command}
    code = 0
    try:
        if any(os.environ.get(key) for key in ("DEBUG", "PWDEBUG", "SSLKEYLOGFILE")):
            raise ConfigurationError("disable_browser_debug_and_tls_key_logging")
        portal_url(args.portal_path)
        if args.headed and args.command == "verify":
            raise ConfigurationError("verify_requires_headless_browsers")
        with private_state(args.state_dir) as profile:
            result["state_id"] = hashlib.sha256(str(profile.resolve()).encode()).hexdigest()[:12]
            interactive = args.command == "login"
            if interactive:
                print(
                    "Sign in in the dedicated Chromium window, then open your timetable. "
                    "Enter credentials and MFA only in the browser. It closes after validation.",
                    file=sys.stderr,
                )
            runs = []
            # verify always uses two separately launched and gracefully closed browsers.
            for _ in range(2 if args.command == "verify" else 1):
                runs.append(browser_run(
                    profile,
                    interactive=interactive,
                    bootstrap_seconds=args.bootstrap_seconds or (900 if interactive else 60),
                    request_seconds=args.request_seconds,
                    samples=max(2, args.samples) if args.command == "verify" else args.samples,
                    sample_interval=args.sample_interval,
                    portal_path=args.portal_path,
                    headed=args.headed,
                ))
                # Retain sanitized evidence of earlier runs if a later run fails.
                result["browser_runs"] = runs.copy()
            result.update({"ok": True, "category": "validated_snapshot", "browser_runs": runs})
            if args.command == "verify":
                result["restart_reuse_demonstrated"] = True
                result["unattended_retrieval_demonstrated"] = True
    except M1Error as error:
        result.update({"category": error.category, "reason": error.reason})
        if error.diagnostics is not None:
            result["diagnostics"] = error.diagnostics
        code = int(error.code)
    except OSError:
        result.update({"category": "configuration", "reason": "local_io_failed"})
        code = 6
    except KeyboardInterrupt:
        result.update({"category": "interrupted", "reason": "operator_interrupted"})
        code = 130
    except Exception:
        # Fail closed, without a traceback potentially containing secrets.
        result.update({"category": "configuration", "reason": "unexpected_local_error"})
        code = 6
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(result, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
