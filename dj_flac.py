"""Keep optimized FLAC24 next to library MP3 320k, with a quota and snapshot unlink."""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("dj666.flac")

FLAC_NAME_RE = re.compile(r"^[0-9a-f]{32}\.flac$")
DEFAULT_QUOTA_BYTES = 1000 * 1024 * 1024 * 1024  # 1000 GiB (R2); editable in admin
PRUNE_LOG_MAX = 40


def fmt_bytes(n: int) -> str:
    n = int(n or 0)
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if abs(n) >= size:
            return f"{n / size:.2f} {unit}"
    return f"{n} B"


def flac_dir(upload_dir: Path) -> Path:
    path = Path(upload_dir) / "flac"
    path.mkdir(parents=True, exist_ok=True)
    return path


def flac_quota_bytes(get_setting) -> int:
    raw = (get_setting("flac_quota_gb", "1000") or "1000").strip()
    try:
        gb = float(raw)
    except ValueError:
        gb = 1000.0
    gb = max(0.5, min(gb, 10000.0))
    return int(gb * 1024 ** 3)


def unlink_with_snapshots(path: Path, snap_roots: list[Path]) -> int:
    """Unlink a live file and any hardlinked copies under auto-backup snapshots.

    Returns the number of directory entries removed. Removing snapshot links is
    what actually frees space when rsync --link-dest was used.
    """
    path = Path(path)
    removed = 0
    name = path.name
    targets: list[Path] = []
    if path.is_file():
        targets.append(path)
    for root in snap_roots:
        if not root or not Path(root).is_dir():
            continue
        # Typical layout: /opt/dj666_backups/auto/<stamp>/uploads/flac/<name>
        for cand in Path(root).glob(f"*/uploads/flac/{name}"):
            targets.append(cand)
        # Some snapshots copy the tree at the app root.
        for cand in Path(root).glob(f"*/flac/{name}"):
            targets.append(cand)
    seen: set[str] = set()
    for p in targets:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def attach_flac(src: Path, upload_dir: Path) -> tuple[str, int]:
    """Copy src into uploads/flac/ and return (filename, size)."""
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(str(src))
    dest_dir = flac_dir(upload_dir)
    name = f"{uuid.uuid4().hex}.flac"
    dest = dest_dir / name
    data = src.read_bytes()
    dest.write_bytes(data)
    try:
        dest.chmod(0o644)
    except OSError:
        pass
    return name, dest.stat().st_size


def resolve_flac(upload_dir: Path, name: Optional[str]) -> Optional[Path]:
    if not name or not FLAC_NAME_RE.match(name):
        return None
    path = (flac_dir(upload_dir) / name).resolve()
    if path.parent != flac_dir(upload_dir).resolve() or not path.is_file():
        return None
    return path


def drop_track_flac(track, upload_dir: Path, snap_roots: list[Path], remote_delete=None) -> int:
    """Clear FLAC fields on track and unlink file + snapshot links (and R2 if given)."""
    name = getattr(track, "flac_path", None)
    path = resolve_flac(upload_dir, name) if name else None
    removed = unlink_with_snapshots(path, snap_roots) if path else 0
    key = getattr(track, "flac_r2_key", None)
    if key and remote_delete:
        try:
            remote_delete(key)
            removed += 1
        except Exception:
            log.exception("remote FLAC delete failed")
    if hasattr(track, "flac_r2_key"):
        track.flac_r2_key = None
    track.flac_path = None
    track.flac_size = None
    track.flac_added_at = None
    track.flac_pinned = False
    return removed


def has_flac(track) -> bool:
    return bool(getattr(track, "flac_path", None) or getattr(track, "flac_r2_key", None))


def flac_usage(tracks) -> tuple[int, int, int]:
    """Return (count, total_bytes, pinned_count) for local and R2 FLACs."""
    count = total = pinned = 0
    for t in tracks:
        if not has_flac(t):
            continue
        count += 1
        total += int(getattr(t, "flac_size", 0) or 0)
        if getattr(t, "flac_pinned", False):
            pinned += 1
    return count, total, pinned


def append_prune_log(get_setting, set_setting, entry: dict) -> None:
    raw = get_setting("flac_prune_log", "[]") or "[]"
    try:
        items = json.loads(raw)
        if not isinstance(items, list):
            items = []
    except json.JSONDecodeError:
        items = []
    items.append(entry)
    items = items[-PRUNE_LOG_MAX:]
    set_setting("flac_prune_log", json.dumps(items, ensure_ascii=False))


def enforce_flac_quota(
    db,
    tracks,
    upload_dir: Path,
    snap_roots: list[Path],
    quota: int,
    min_free: int,
    get_setting,
    set_setting,
    remote_delete=None,
) -> list[dict]:
    """Delete oldest unpinned FLACs until under quota and disk has min_free.

    Disk-low pruning only removes local files, never R2 objects.
    Quota pruning counts and can delete both local and R2 FLACs.
    """
    def disk_free() -> int:
        import shutil
        try:
            return shutil.disk_usage(str(upload_dir)).free
        except OSError:
            return min_free

    pruned: list[dict] = []
    holders = [t for t in tracks if has_flac(t)]

    def total_size() -> int:
        return sum(int(getattr(t, "flac_size", 0) or 0) for t in holders if has_flac(t))

    def need_quota() -> bool:
        return total_size() > quota

    def need_disk() -> bool:
        return disk_free() < min_free

    if not need_quota() and not need_disk():
        return pruned

    def added_at(t):
        return getattr(t, "flac_added_at", None) or getattr(t, "created_at", None) or datetime.now(timezone.utc)

    def prune_list(victims, why: str, remote=None) -> None:
        victims = list(victims)
        victims.sort(key=added_at)
        for track in victims:
            if why == "quota" and not need_quota():
                break
            if why == "disk" and not need_disk():
                break
            title = (getattr(track, "title", None) or "")[:80]
            size = int(getattr(track, "flac_size", 0) or 0)
            tid = getattr(track, "id", None)
            # Disk-low: drop only the local file, keep an R2 copy if present.
            if why == "disk":
                path = resolve_flac(upload_dir, getattr(track, "flac_path", None))
                removed = unlink_with_snapshots(path, snap_roots) if path else 0
                track.flac_path = None
                if not getattr(track, "flac_r2_key", None):
                    track.flac_size = None
                    track.flac_added_at = None
                    track.flac_pinned = False
            else:
                removed = drop_track_flac(track, upload_dir, snap_roots, remote_delete=remote)
            entry = {
                "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "track_id": tid,
                "title": title,
                "size": size,
                "unlinked": removed,
                "why": why,
            }
            pruned.append(entry)
            log.info("flac prune track=%s size=%s why=%s unlinked=%s", tid, size, why, removed)
            append_prune_log(get_setting, set_setting, entry)

    if need_disk():
        local_victims = [
            t for t in holders
            if getattr(t, "flac_path", None) and not getattr(t, "flac_pinned", False)
        ]
        prune_list(local_victims, "disk", remote=None)
    if need_quota():
        quota_victims = [
            t for t in holders
            if has_flac(t) and not getattr(t, "flac_pinned", False)
        ]
        prune_list(quota_victims, "quota", remote=remote_delete)
    return pruned
