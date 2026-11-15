"""Cloudflare R2 object storage helpers for DJ666 library audio.

Used only when R2_ENABLED=1 and the required env vars are present.
Never logs access keys or secrets.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import unicodedata
from pathlib import Path
from typing import Optional

log = logging.getLogger("dj666.r2")

REQUIRED = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
DEFAULT_ENV_PATHS = (
    Path("/opt/dj666/.env"),
    Path(__file__).resolve().parent / ".env",
)
_CLIENT = None


def load_env_file(path: Optional[Path] = None) -> bool:
    """Load KEY=VALUE lines into os.environ if the key is not already set.

    Returns True if a file was read. Values are never logged.
    """
    paths = [path] if path is not None else list(DEFAULT_ENV_PATHS)
    loaded = False
    for p in paths:
        if not p or not Path(p).is_file():
            continue
        try:
            text = Path(p).read_text(encoding="utf-8")
        except OSError:
            log.warning("r2 env file is not readable")
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = val
        loaded = True
        break
    return loaded


def r2_enabled() -> bool:
    if (os.environ.get("R2_ENABLED") or "").strip() != "1":
        return False
    return all((os.environ.get(k) or "").strip() for k in REQUIRED)


def public_base() -> str:
    d = (os.environ.get("R2_PUBLIC_DOMAIN") or "cdn.dj666.pw").strip().rstrip("/")
    if d and not d.startswith("http://") and not d.startswith("https://"):
        d = "https://" + d
    return d or "https://cdn.dj666.pw"


def public_url(key: str) -> str:
    return f"{public_base()}/{str(key).lstrip('/')}"


def bucket() -> str:
    return (os.environ.get("R2_BUCKET") or "dj666").strip()


def _client():
    global _CLIENT
    if not r2_enabled():
        raise RuntimeError("R2 is not enabled")
    if _CLIENT is not None:
        return _CLIENT
    import boto3
    from botocore.config import Config

    account = (os.environ.get("R2_ACCOUNT_ID") or "").strip()
    _CLIENT = boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=(os.environ.get("R2_ACCESS_KEY_ID") or "").strip(),
        aws_secret_access_key=(os.environ.get("R2_SECRET_ACCESS_KEY") or "").strip(),
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )
    return _CLIENT


def reset_client() -> None:
    global _CLIENT
    _CLIENT = None


def upload_file(local, key: str, content_type: Optional[str] = None, cache_control: Optional[str] = None) -> None:
    extra = {}
    if content_type:
        extra["ContentType"] = content_type
    extra["CacheControl"] = cache_control or "public, max-age=31536000, immutable"
    _client().upload_file(str(local), bucket(), key, ExtraArgs=extra)


def head(key: str) -> dict:
    return _client().head_object(Bucket=bucket(), Key=key)


def object_size(key: str) -> Optional[int]:
    try:
        info = head(key)
    except Exception:  # noqa: BLE001
        return None
    try:
        return int(info.get("ContentLength") or 0)
    except (TypeError, ValueError):
        return None


def delete(key: str) -> None:
    if not key:
        return
    _client().delete_object(Bucket=bucket(), Key=key)


def presigned_url(key: str, expires: int = 600, filename: Optional[str] = None, content_type: Optional[str] = None) -> str:
    params = {"Bucket": bucket(), "Key": key}
    if filename:
        safe = re.sub(r'[\r\n"]+', "", filename)[:180] or "audio.flac"
        params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'
    if content_type:
        params["ResponseContentType"] = content_type
    return _client().generate_presigned_url("get_object", Params=params, ExpiresIn=int(expires))


def mp3_key(track_id: int, title: str = "") -> str:
    raw = unicodedata.normalize("NFKD", title or "")
    ascii_part = "".join(ch for ch in raw.encode("ascii", "ignore").decode("ascii") if ch.isalnum() or ch in "-_")
    ascii_part = re.sub(r"[-_]+", "-", ascii_part).strip("-").lower()[:40]
    if not ascii_part:
        ascii_part = "audio"
    return f"mp3/{int(track_id)}-{ascii_part}.mp3"


def pending_mp3_key(track_id: int) -> str:
    """Unguessable key: the custom domain publishes the whole bucket."""
    return f"pending/{int(track_id)}-{secrets.token_hex(16)}.mp3"


def is_pending_key(key: Optional[str]) -> bool:
    return str(key or "").startswith("pending/")


def flac_key(track_id: int) -> str:
    # Random token: the custom domain publishes the whole bucket, so FLAC paths must not be guessable.
    return f"flac/{int(track_id)}-{secrets.token_hex(16)}.flac"


def optimize_mp3_key(job_id: str) -> str:
    return f"optimize/{job_id}/optimized.mp3"


def optimize_flac_key(job_id: str) -> str:
    # Unguessable suffix: the public bucket should not make pending FLACs easy to list-guess.
    return f"optimize/{job_id}/optimized-{secrets.token_hex(16)}.flac"


def copy_object(src_key: str, dst_key: str, content_type: Optional[str] = None, cache_control: Optional[str] = None) -> None:
    """Server-side copy inside the same bucket (no local download).

    Uses boto3's managed copy(), which automatically switches to multipart
    copy for objects over 5GB (a single CopyObject call cannot).
    """
    src_key = str(src_key).lstrip("/")
    dst_key = str(dst_key).lstrip("/")
    if not src_key or not dst_key or src_key == dst_key:
        return
    extra: dict = {}
    if content_type or cache_control:
        if content_type:
            extra["ContentType"] = content_type
        extra["CacheControl"] = cache_control or "public, max-age=31536000, immutable"
        extra["MetadataDirective"] = "REPLACE"
    _client().copy(
        {"Bucket": bucket(), "Key": src_key},
        bucket(),
        dst_key,
        ExtraArgs=extra or None,
    )


def usage_bytes(prefix: str = "") -> Optional[int]:
    """Sum ContentLength under an optional prefix. Returns None on failure."""
    try:
        client = _client()
        total = 0
        token = None
        while True:
            kwargs = {"Bucket": bucket()}
            if prefix:
                kwargs["Prefix"] = prefix
            if token:
                kwargs["ContinuationToken"] = token
            resp = client.list_objects_v2(**kwargs)
            for item in resp.get("Contents") or []:
                total += int(item.get("Size") or 0)
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
            if not token:
                break
        return total
    except Exception:  # noqa: BLE001
        log.exception("r2 usage listing failed")
        return None


def put_cors(origins=None) -> None:
    """Allow browser GET/HEAD of public objects from the site origins (needed for Web Audio / mixer)."""
    origins = list(origins or ("https://www.dj666.pw", "https://dj666.pw"))
    _client().put_bucket_cors(
        Bucket=bucket(),
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedOrigins": origins,
                    "AllowedMethods": ["GET", "HEAD"],
                    "AllowedHeaders": ["Range", "Content-Type", "Authorization", "*"],
                    "ExposeHeaders": ["ETag", "Content-Length", "Content-Range", "Accept-Ranges", "Content-Type"],
                    "MaxAgeSeconds": 86400,
                }
            ]
        },
    )
