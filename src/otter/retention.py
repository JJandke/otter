"""Explicit quarantine-only payload expiry; observation records are never pruned."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ._store_files import StorageError, storage_errors
from .backups import _create_backup
from .storage import Store, _time


ELIGIBLE = """SELECT c.content_id,c.event_count FROM snapshot_content c
    WHERE c.expired_at_utc_us IS NULL AND c.created_at_utc_us < ?
      AND EXISTS (SELECT 1 FROM fetch_attempt a WHERE a.content_id=c.content_id AND a.status='suspicious')
      AND NOT EXISTS (SELECT 1 FROM fetch_attempt a WHERE a.content_id=c.content_id
        AND (a.status!='suspicious' OR a.received_at_utc_us >= ? OR a.finished_at_utc_us >= ?))
      AND NOT EXISTS (SELECT 1 FROM fetch_attempt a WHERE a.status='started'
        AND a.capture_digest=c.digest AND a.capture_normalization_version=c.normalization_version
        AND a.capture_format_version=c.format_version)
    ORDER BY c.content_id"""


def prune_quarantine(directory: Path, *, older_than_days: int = 30, apply: bool = False,
                     now: datetime | None = None) -> dict:
    if type(older_than_days) is not int or not 1 <= older_than_days <= 36500 or type(apply) is not bool:
        raise StorageError("invalid_retention_policy")
    current = datetime.now(timezone.utc) if now is None else now
    instant = _time(current)
    try:
        cutoff = _time(current - timedelta(days=older_than_days))
    except (OverflowError, TypeError):
        raise StorageError("invalid_retention_policy") from None
    with storage_errors(), Store(directory) as store, store.exclusive():
        connection = store._db()
        rows = connection.execute(ELIGIBLE, (cutoff, cutoff, cutoff)).fetchall()
        result = {"dry_run": not apply, "older_than_days": older_than_days,
                  "eligible_content_count": len(rows), "eligible_event_rows": sum(row[1] for row in rows),
                  "expired_content_count": 0}
        if not apply or not rows:
            return result
        backup = _create_backup(connection, store.directory)
        with store.transaction() as db:
            for row in rows:
                db.execute("DELETE FROM snapshot_event WHERE content_id=?", (row[0],))
                db.execute("UPDATE snapshot_content SET expired_at_utc_us=? WHERE content_id=?", (instant, row[0]))
        return {**result, "expired_content_count": len(rows), "backup_name": backup["backup_name"]}
