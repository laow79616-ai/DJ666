#!/usr/bin/env python3
"""DJ666 automatic backup. Run as root via systemd (Nice/IOScheduling=idle).

Never logs secret file contents (.env, session keys, api tokens).
Uses SQLite's online backup API (no sqlite3 CLI required) and rsync
--link-dest hardlinks so unchanged uploads take no extra disk.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ_MYT = timezone(timedelta(hours=8))
UTC = timezone.utc

APP = Path("/opt/dj666")
DEST_ROOT = Path("/opt/dj666_backups")
AUTO = DEST_ROOT / "auto"
SNAPS = AUTO / "snaps"
LOG = AUTO / "backup.log.jsonl"
STATUS = AUTO / "status.json"
LOCK = AUTO / "backup.lock"
MIN_FREE = 3 * 1024 * 1024 * 1024
MAX_ATTEMPTS = 3  # first try + 2 retries
BACKOFF = (10, 30)
SECRET_NAMES = {
    ".env", "tg_session.enc", "tg_session.key", "tg_user.json",
}

NGINX_SRC = Path("/etc/nginx/sites-available/dj666.pw")
UNIT_SRC = Path("/etc/systemd/system/dj666.service")


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def myt(dt: datetime) -> str:
    return dt.astimezone(TZ_MYT).strftime("%Y-%m-%d %H:%M:%S") + " MYT"


def disk_free(path: Path | None = None) -> int:
    try:
        return shutil.disk_usage(str(path or DEST_ROOT if DEST_ROOT.exists() else "/")).free
    except OSError:
        return shutil.disk_usage("/").free


def fmt_bytes(n: int) -> str:
    n = int(n or 0)
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if abs(n) >= size:
            return f"{n / size:.2f} {unit}"
    return f"{n} B"


def ensure_dirs() -> None:
    DEST_ROOT.mkdir(parents=True, exist_ok=True)
    AUTO.mkdir(parents=True, exist_ok=True)
    SNAPS.mkdir(parents=True, exist_ok=True)
    for p in (DEST_ROOT, AUTO, SNAPS):
        try:
            os.chown(p, 0, 33)  # root:www-data
        except OSError:
            pass
        os.chmod(p, 0o750)


def write_private(path: Path, data: bytes, mode: int = 0o640) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(tmp, mode)
    try:
        os.chown(tmp, 0, 33)
    except OSError:
        pass
    os.replace(tmp, path)


def append_jsonl(record: dict) -> None:
    AUTO.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    fd = os.open(str(LOG), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chown(LOG, 0, 33)
        os.chmod(LOG, 0o640)
    except OSError:
        pass


def write_status(record: dict) -> None:
    payload = {"last": record, "updated": record.get("end") or iso(now_utc())}
    write_private(STATUS, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"), 0o640)


def list_snaps() -> list[tuple[datetime, Path]]:
    out = []
    if not SNAPS.is_dir():
        return out
    for p in SNAPS.iterdir():
        if not p.is_dir():
            continue
        try:
            ts = datetime.strptime(p.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            continue
        out.append((ts, p))
    out.sort(key=lambda x: x[0])
    return out


def prune(reason: str = "retention") -> list[str]:
    """Apply retention and optional extra prune. Returns deleted snapshot names."""
    snaps = list_snaps()
    if not snaps:
        return []
    now = now_utc()
    keep: set[Path] = set()
    # always keep the newest
    keep.add(snaps[-1][1])
    hourly: dict[str, Path] = {}
    daily: dict[str, Path] = {}
    for ts, path in snaps:
        age = now - ts
        local = ts.astimezone(TZ_MYT)
        if age <= timedelta(hours=24):
            keep.add(path)
        elif age <= timedelta(days=3):
            hourly[local.strftime("%Y-%m-%d %H")] = path  # last write wins (sorted asc)
        elif age <= timedelta(days=14):
            daily[local.strftime("%Y-%m-%d")] = path
        # older than 14 days: not kept unless it's the newest
    keep.update(hourly.values())
    keep.update(daily.values())
    deleted = []
    for ts, path in snaps:
        if path in keep:
            continue
        try:
            shutil.rmtree(path, ignore_errors=False)
            deleted.append(path.name)
        except OSError as exc:
            print(f"[dj666-backup] prune failed {path.name}: {type(exc).__name__}", flush=True)
    if deleted:
        print(f"[dj666-backup] pruned {len(deleted)} snapshots ({reason})", flush=True)
    return deleted


def measure(path: Path) -> dict:
    files = 0
    apparent = 0
    unique = 0
    shared = 0
    for root, dirs, names in os.walk(path):
        # include dir metadata only indirectly
        for name in names:
            fp = Path(root) / name
            try:
                st = fp.lstat()
            except OSError:
                continue
            files += 1
            if stat.S_ISLNK(st.st_mode):
                continue
            apparent += st.st_size
            if st.st_nlink > 1:
                shared += st.st_size
            else:
                unique += st.st_size
    # du of the snap (hardlinks inside this tree counted once by du)
    disk = 0
    try:
        p = subprocess.run(["du", "-sb", str(path)], capture_output=True, text=True, timeout=600)
        if p.returncode == 0 and p.stdout:
            disk = int(p.stdout.split()[0])
    except Exception:
        disk = unique  # fallback
    return {
        "files": files,
        "bytes_apparent": apparent,
        "bytes_unique": unique,
        "bytes_shared": shared,
        "bytes_disk": disk,
    }


def chown_tree(path: Path, uid: int = 0, gid: int = 33) -> None:
    try:
        os.chown(path, uid, gid)
    except OSError:
        pass
    for root, dirs, names in os.walk(path):
        for d in dirs:
            try:
                os.chown(os.path.join(root, d), uid, gid)
            except OSError:
                pass
        for n in names:
            try:
                os.chown(os.path.join(root, n), uid, gid)
            except OSError:
                pass


def secure_file(path: Path, mode: int | None = None) -> None:
    try:
        if mode is None:
            mode = path.stat().st_mode & 0o777
        os.chmod(path, mode)
        os.chown(path, 0, 33)
    except OSError:
        try:
            os.chmod(path, mode if mode is not None else 0o600)
        except OSError:
            pass


def copy_file(src: Path, dst: Path, force_mode: int | None = None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if force_mode is not None or src.name in SECRET_NAMES:
        secure_file(dst, force_mode or 0o600)
    else:
        # preserve mode but re-own
        secure_file(dst, src.stat().st_mode & 0o777)


def copy_tree(src: Path, dst: Path) -> int:
    """Copy a code/config tree; skip __pycache__ and .pyc."""
    count = 0
    if dst.exists():
        shutil.rmtree(dst)
    for root, dirs, names in os.walk(src):
        dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git"}]
        rel = os.path.relpath(root, src)
        target_dir = dst if rel == "." else dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(target_dir, 0o750)
        for name in names:
            if name.endswith((".pyc", ".pyo")):
                continue
            s = Path(root) / name
            if s.is_symlink() or not s.is_file():
                continue
            copy_file(s, target_dir / name)
            count += 1
    return count


def sqlite_backup(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
    try:
        dst_conn = sqlite3.connect(str(dst), timeout=60)
        try:
            src_conn.backup(dst_conn)
            dst_conn.commit()
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    secure_file(dst, 0o600)
    chk = sqlite3.connect(f"file:{dst}?mode=ro", uri=True, timeout=30)
    try:
        row = chk.execute("PRAGMA integrity_check").fetchone()
        result = (row[0] if row else "empty") or "empty"
    finally:
        chk.close()
    if result != "ok":
        raise RuntimeError(f"app.db integrity_check failed: {result}")
    return result


def rsync_uploads(dest_uploads: Path, prev_uploads: Path | None) -> None:
    dest_uploads.mkdir(parents=True, exist_ok=True)
    cmd = [
        "rsync", "-a", "--numeric-ids", "--delete", "--delete-excluded",
        "--exclude", "/optimize/",
        "--exclude", "/optimize/**",
        "--link-dest", str(APP / "uploads"),
    ]
    if prev_uploads and prev_uploads.is_dir():
        cmd.extend(["--link-dest", str(prev_uploads)])
    cmd.extend([str(APP / "uploads") + "/", str(dest_uploads) + "/"])
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1500)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip().splitlines()
        tail = " ".join(err[-5:])[:400] if err else f"exit {p.returncode}"
        raise RuntimeError(f"rsync uploads failed: {tail}")


def copy_data(dest_data: Path) -> None:
    dest_data.mkdir(parents=True, exist_ok=True)
    os.chmod(dest_data, 0o750)
    src = APP / "data"
    skip = {"app.db", "app.db-wal", "app.db-shm", "app.db-journal"}
    if not src.is_dir():
        return
    for item in src.iterdir():
        if item.name in skip:
            continue
        if item.is_dir():
            # include small helper dirs (e.g. dup_staging); skip obvious caches
            if item.name in {"__pycache__"}:
                continue
            copy_tree(item, dest_data / item.name)
        elif item.is_file():
            mode = 0o600 if (item.name in SECRET_NAMES or (item.stat().st_mode & 0o077) == 0) else None
            copy_file(item, dest_data / item.name, force_mode=mode)


def do_backup(snap_name: str, prev: Path | None) -> dict:
    snap = SNAPS / snap_name
    if snap.exists():
        shutil.rmtree(snap)
    snap.mkdir(parents=True, exist_ok=True)
    os.chmod(snap, 0o750)
    counts: dict[str, int] = {}

    db_dst = snap / "db" / "app.db"
    integrity = sqlite_backup(APP / "data" / "app.db", db_dst)
    counts["db"] = 1

    copy_data(snap / "data")

    code = snap / "code"
    code.mkdir(parents=True, exist_ok=True)
    if (APP / "main.py").is_file():
        copy_file(APP / "main.py", code / "main.py")
        counts["main.py"] = 1
    if (APP / "requirements.txt").is_file():
        copy_file(APP / "requirements.txt", code / "requirements.txt")
    if (APP / "templates").is_dir():
        counts["templates"] = copy_tree(APP / "templates", code / "templates")
    if (APP / "static").is_dir():
        counts["static"] = copy_tree(APP / "static", code / "static")
    if (APP / "scripts").is_dir():
        counts["scripts"] = copy_tree(APP / "scripts", code / "scripts")
    # checked-in copies of unit/nginx if present in the app dir
    for name in ("dj666.nginx.conf", "dj666.service"):
        src = APP / name
        if src.is_file():
            copy_file(src, code / name)

    etc = snap / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    if UNIT_SRC.is_file():
        copy_file(UNIT_SRC, etc / "dj666.service")
    if NGINX_SRC.is_file():
        copy_file(NGINX_SRC, etc / "nginx-dj666.pw.conf")
    # timer/service units if already installed
    for extra in ("dj666-backup.service", "dj666-backup.timer"):
        src = Path("/etc/systemd/system") / extra
        if src.is_file():
            copy_file(src, etc / extra)
    env_src = APP / ".env"
    if env_src.is_file():
        copy_file(env_src, snap / "env" / ".env", force_mode=0o600)

    prev_uploads = (prev / "uploads") if prev else None
    rsync_uploads(snap / "uploads", prev_uploads if prev_uploads and prev_uploads.is_dir() else None)

    chown_tree(snap)
    os.chmod(snap, 0o750)
    stats = measure(snap)
    meta = {
        "name": snap_name,
        "created_utc": iso(now_utc()),
        "created_myt": myt(now_utc()),
        "path": str(snap),
        "integrity": integrity,
        "counts": counts,
        "stats": stats,
        "excluded": ["uploads/optimize", "venv", "__pycache__", "/opt/dj666/backups (manual)"],
        "included": [
            "data/app.db (sqlite backup + integrity_check)",
            "data/ (secrets copied with mode 600)",
            ".env, systemd unit, nginx site",
            "main.py, templates/, static/, scripts/",
            "uploads/ (rsync hardlink snapshot, optimize excluded)",
        ],
    }
    write_private(snap / "meta.json", json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"), 0o640)
    return {"snap": str(snap), "name": snap_name, "integrity": integrity, **stats, "counts": counts}


def run_once() -> dict:
    ensure_dirs()
    pruned = prune("retention")
    free = disk_free()
    if free < MIN_FREE:
        # try harder prune of older-than-24h snaps, keeping newest
        extra = prune("low-disk")
        pruned.extend(extra)
        free = disk_free()
    if free < MIN_FREE:
        rec = {
            "result": "skip",
            "error": f"磁盘剩余 {fmt_bytes(free)}，低于 3GB 保护线，已跳过本次备份",
            "pruned": pruned,
            "disk_free_before": free,
        }
        return rec
    snaps = list_snaps()
    prev = snaps[-1][1] if snaps else None
    snap_name = now_utc().strftime("%Y%m%dT%H%M%SZ")
    # avoid name collision if rerun in the same second
    if (SNAPS / snap_name).exists():
        snap_name = now_utc().strftime("%Y%m%dT%H%M%S") + f"{now_utc().microsecond // 1000:03d}Z"
    info = do_backup(snap_name, prev)
    # current symlink for convenience
    current = AUTO / "current"
    try:
        if current.is_symlink() or current.exists():
            current.unlink()
        current.symlink_to(Path("snaps") / snap_name)
        os.chown(current, 0, 33, follow_symlinks=False)
    except OSError:
        pass
    info["pruned"] = pruned
    info["disk_free_after"] = disk_free()
    info["result"] = "ok"
    info["error"] = ""
    return info


def main() -> int:
    if os.geteuid() != 0:
        print("[dj666-backup] must run as root", file=sys.stderr)
        return 2
    ensure_dirs()
    start = now_utc()
    lock_fd = os.open(str(LOCK), os.O_CREAT | os.O_RDWR, 0o640)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        rec = {
            "start": iso(start),
            "end": iso(now_utc()),
            "start_myt": myt(start),
            "end_myt": myt(now_utc()),
            "result": "skip",
            "error": "已有备份任务在运行，本次跳过",
            "seconds": 0,
            "attempts": 0,
        }
        append_jsonl(rec)
        write_status(rec)
        print("[dj666-backup] skipped: already running", flush=True)
        return 0
    try:
        os.chmod(LOCK, 0o640)
        try:
            os.chown(LOCK, 0, 33)
        except OSError:
            pass
        last_err = ""
        result = None
        attempts = 0
        for attempts in range(1, MAX_ATTEMPTS + 1):
            try:
                result = run_once()
                if result.get("result") == "skip":
                    break
                result["result"] = "ok"
                last_err = ""
                break
            except Exception as exc:  # noqa: BLE001
                last_err = f"{type(exc).__name__}: {exc}"
                print(f"[dj666-backup] attempt {attempts} failed: {type(exc).__name__}", flush=True)
                # do not dump tracebacks that might include paths with secrets beyond typenames
                if attempts < MAX_ATTEMPTS:
                    time.sleep(BACKOFF[min(attempts - 1, len(BACKOFF) - 1)])
        end = now_utc()
        rec = {
            "start": iso(start),
            "end": iso(end),
            "start_myt": myt(start),
            "end_myt": myt(end),
            "seconds": round((end - start).total_seconds(), 2),
            "attempts": attempts,
            "disk_free_before": disk_free(),
        }
        if result:
            rec.update(result)
        if last_err and rec.get("result") != "skip":
            rec["result"] = "fail"
            rec["error"] = last_err[:400]
        rec.setdefault("error", last_err[:400] if last_err else "")
        rec.setdefault("result", "fail" if last_err else "ok")
        rec["disk_free_after"] = disk_free()
        append_jsonl(rec)
        write_status(rec)
        print(
            f"[dj666-backup] {rec['result']} in {rec['seconds']}s "
            f"unique={fmt_bytes(rec.get('bytes_unique') or 0)} "
            f"files={rec.get('files') or 0}",
            flush=True,
        )
        return 0 if rec.get("result") in {"ok", "skip"} else 1
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        # last-resort: never print secret values
        print(f"[dj666-backup] fatal {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
