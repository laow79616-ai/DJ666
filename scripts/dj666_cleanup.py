#!/usr/bin/env python3
"""Periodic DJ666 temp / optimize-job cleaner.

Safe: never deletes library tracks, announcement media, data/, or other projects.
Intended to run as root (backup timer ExecStartPre) or www-data.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

APP = Path("/opt/dj666")
UPLOAD = APP / "uploads"
OPT = UPLOAD / "optimize"
DB = APP / "data" / "app.db"
LOG = Path("/opt/dj666_backups/auto/cleanup.log.jsonl")
OPT_ID_RE = __import__("re").compile(r"^[a-f0-9]{24}$")
KEEP_DONE_SEC = 12 * 3600
KEEP_FAILED_SEC = 2 * 3600
STALE_UPLOAD_SEC = 6 * 3600
ACTIVE = {"uploading", "uploaded", "queued", "running", "downloading"}
TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S%z")


def dir_bytes(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def load_job(d: Path) -> dict | None:
    meta = d / "job.json"
    try:
        raw = json.loads(meta.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return None


def library_exists(con: sqlite3.Connection | None, track_id) -> bool:
    if not track_id or con is None:
        return False
    try:
        row = con.execute("SELECT filename FROM tracks WHERE id=?", (int(track_id),)).fetchone()
    except (TypeError, ValueError, sqlite3.Error):
        return False
    if not row or not row[0]:
        return False
    return (UPLOAD / row[0]).is_file()


def drop_telegram_orig(d: Path, job: dict) -> int:
    if (job or {}).get("source") != "telegram":
        return 0
    if (job or {}).get("state") != "done":
        return 0
    freed = 0
    for p in d.glob("orig.*"):
        try:
            sz = p.stat().st_size
            p.unlink()
            freed += sz
        except OSError:
            pass
    return freed


def write_log(rec: dict) -> None:
    rec = dict(rec)
    rec.setdefault("ts", now_iso())
    line = json.dumps(rec, ensure_ascii=False)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def cleanup() -> dict:
    now = time.time()
    con = None
    if DB.is_file():
        try:
            con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        except sqlite3.Error:
            con = None
    purged = []
    orig_dropped = []
    skipped_active = []
    try:
        names = list(OPT.iterdir()) if OPT.is_dir() else []
    except OSError:
        names = []
    for d in names:
        if not d.is_dir() or not OPT_ID_RE.match(d.name):
            continue
        job = load_job(d)
        state = (job or {}).get("state") or ""
        try:
            age_ref = float((job or {}).get("updated") or d.stat().st_mtime)
            created = float((job or {}).get("created") or age_ref)
        except (TypeError, ValueError, OSError):
            continue
        reason = None
        if job and job.get("track_id") and library_exists(con, job.get("track_id")):
            reason = "imported"
        elif state in ("uploading", "uploaded", "downloading") and now - age_ref > STALE_UPLOAD_SEC:
            reason = "stale_upload"
        elif job is None and now - age_ref > STALE_UPLOAD_SEC:
            reason = "stale_no_job"
        elif state == "failed" and now - created > KEEP_FAILED_SEC:
            reason = "failed_expired"
        elif state not in ACTIVE and now - created > KEEP_DONE_SEC:
            reason = "older_than_12h"
        if reason:
            sz = dir_bytes(d)
            shutil.rmtree(d, ignore_errors=True)
            purged.append({"id": d.name, "reason": reason, "bytes": sz, "state": state,
                           "track_id": (job or {}).get("track_id")})
            continue
        if state in ACTIVE:
            skipped_active.append(d.name)
            continue
        freed = drop_telegram_orig(d, job or {})
        if freed:
            orig_dropped.append({"id": d.name, "bytes": freed})
    if con is not None:
        try:
            con.close()
        except sqlite3.Error:
            pass
    rec = {
        "result": "ok",
        "purged": purged,
        "purged_count": len(purged),
        "purged_bytes": sum(x["bytes"] for x in purged),
        "orig_dropped": orig_dropped,
        "orig_dropped_bytes": sum(x["bytes"] for x in orig_dropped),
        "skipped_active": skipped_active,
    }
    write_log(rec)
    return rec


def main() -> int:
    try:
        cleanup()
    except Exception as exc:  # noqa: BLE001 — never block the backup timer
        write_log({"result": "error", "error": type(exc).__name__})
    return 0


if __name__ == "__main__":
    sys.exit(main())
