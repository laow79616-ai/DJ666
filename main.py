#!/usr/bin/env python3
"""DJ666 音乐随听平台。"""

from __future__ import annotations

import asyncio
import calendar
import hashlib
import html as html_lib
import json
import concurrent.futures
import logging
import math
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
import secrets
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import dj_flac
import r2_storage
import tg_filter_v3 as tgf3
import queue as queue_mod

# Raise Starlette multipart part limit before form parsers are used.
# Starlette 1.10 defaults to 1MB, which would reject music packs.
os.environ.setdefault("STARLETTE_MAX_FORM_PART_SIZE", str(210 * 1024 * 1024))

import bcrypt
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    or_,
    func,
    select,
    text,
)
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    joinedload,
    mapped_column,
    relationship,
    Session,
    sessionmaker,
)
from markupsafe import Markup, escape
from starlette.formparsers import MultiPartException
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request as StarletteRequest

try:  # Pillow is optional; image filter rules need it.
    from PIL import Image as PILImage
except Exception:  # noqa: BLE001
    PILImage = None

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
FLAC_DIR = UPLOAD_DIR / "flac"
FLAC_SNAP_ROOTS = [Path("/opt/dj666_backups/auto")]
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
ENV_FILE = BASE_DIR / ".env"
DB_PATH = DATA_DIR / "app.db"
TZ_CN = timezone(timedelta(hours=8))
ANN_MEDIA_DIR = UPLOAD_DIR / "announcements"
CHAT_MEDIA_DIR = UPLOAD_DIR / "chat"
CHAT_MAX_MEDIA = 10
ANN_MAX_MEDIA = 10
ANN_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
ANN_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v"}
ANN_MEDIA_EXTS = ANN_IMAGE_EXTS | ANN_VIDEO_EXTS
# Practical ceiling for one announcement media file (Nginx is configured to match).
ANN_MEDIA_MAX_BYTES = int(os.environ.get("ANN_MEDIA_MAX_MB", "4096")) * 1024 * 1024
ANN_DISK_RESERVE_BYTES = 1024 * 1024 * 1024
ANN_PAGE_SIZE = 30
ADMIN_ANN_PAGE_SIZE = 30
ANN_MEDIA_NAME_RE = re.compile(r"^[a-f0-9]{32}\.(jpg|jpeg|png|gif|webp|mp4|webm|mov|m4v)$")
ANN_MEDIA_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".mp4": "video/mp4",
    ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
}
TG_CHANNEL = os.environ.get("TG_CHANNEL", "svip")
TG_PREVIEW_URL = f"https://t.me/s/{TG_CHANNEL}"
TG_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)
TG_DEFAULT_INTERVAL_MIN = 30
TG_INTERVAL_CHOICES = (15, 30, 60, 120)
TG_VIDEO_MAX_BYTES = 300 * 1024 * 1024
TG_KEEP_ITEMS = 2000
TG_COLLECT_MAX_PAGES = 160
TG_COLLECT_PAGE_PAUSE = 0.3
TG_AUTO_PUBLISH_DELAY_DEFAULT = 60
TG_AUTO_PUBLISH_DELAY_CHOICES = (30, 60, 90, 120)
TG_AUTO_PUBLISH_CAP = 50
TG_ENG_RECENT_DAYS = 7
# During a month-long collect, skip full videos larger than this; keep the poster.
TG_VIDEO_COLLECT_MAX_BYTES = 40 * 1024 * 1024
TG_DISK_MIN_FREE = 3 * 1024 * 1024 * 1024
TG_STATUS_NEW = "new"
TG_STATUS_PUBLISHED = "published"
TG_STATUS_IGNORED = "ignored"
TG_FILTER_DIR = UPLOAD_DIR / "tgfilter"
TG_FILTER_NAME_RE = re.compile(r"^[a-f0-9]{32}\.jpg$")
TG_FILTER_PATTERN_MAX = 200
TG_FILTER_SAMPLE_MAX_BYTES = 30 * 1024 * 1024
TG_FILTER_AUTO_MAX = 300
TG_FILTER_THRESHOLDS = {"strict": 6, "normal": 10, "loose": 14}
TG_FILTER_THRESHOLD_LABELS = {"strict": "严格", "normal": "标准", "loose": "宽松"}
# mode "strip": remove the matching promo line/segment, keep the rest of the post.
# mode "block": the whole post is promo/advert and is ignored automatically.
DEFAULT_TG_FILTER_RULES = (
    ("strip", "关注大事件频道➡️ @Svip 投稿：@tx188"),
    ("strip", "关注大事件频道"),
    ("strip", "投稿：@tx188"),
    ("strip", "@Svip"),
    ("strip", "投稿：@"),
    ("block", "#付费广告"),
)
# Additional strip rules (seeded once via tg_filter_seeded_v2). Self-links and leftover contact lines.
DEFAULT_TG_STRIP_RULES_V2 = (
    ("strip", "t.me/svip/"),
    ("strip", "telegram.me/svip/"),
    ("strip", "此前文章："),
    ("strip", "相关文章："),
    ("strip", "此前爆料："),
    ("strip", "此前提示："),
    ("strip", "回复前文："),
    ("strip", "@tx188"),
)
# Gambling-ad keywords (pattern, weight). Detection uses a combined score; weak words alone do not block.
# weight 4 = 强（广告号召，可单独判定），3 = 中，1–2 = 弱（需组合）。
DEFAULT_TG_GAMBLE_RULES = (
    ("返水", 4),
    ("起步返水", 4),
    ("USDT充提", 4),
    ("首充", 4),
    ("日出千万", 4),
    ("点击上桌", 4),
    ("一键试投", 4),
    ("点击加入游戏", 4),
    ("链上U", 4),
    ("自动上下分", 4),
    ("哈希娱乐", 4),
    ("注册即送", 4),
    ("代理加盟", 4),
    ("诚招代理", 4),
    ("打码量", 4),
    ("洗码", 4),
    ("娱乐群组", 4),
    ("官方直营", 4),
    ("真人视讯", 4),
    ("香港六合彩", 4),
    ("在线娱乐城", 4),
    ("体育/真人/棋牌", 4),
    ("百家乐", 3),
    ("三公", 3),
    ("牛牛", 3),
    ("龙虎", 3),
    ("快三", 3),
    ("时时彩", 3),
    ("PC28", 3),
    ("棋牌", 3),
    ("六合彩", 3),
    ("博彩", 2),
    ("彩票", 2),
    ("开奖", 2),
    ("下注", 2),
    ("充提", 3),
    ("点数", 1),
    ("充值", 1),
    ("真人", 1),
    ("体育", 1),
)
TG_GAMBLE_SCORE_MIN = 4
TG_GAMBLE_NEWS_RE = re.compile(
    r"警方|被捕|窝点|电诈|诈骗|检察院|法院|报道|资讯|受害者|查处|扫荡|打击|"
    r"涉嫌|逮捕|通缉|调查组|拘留|扣押|#曝光|#群友爆料|#群友反馈|#群友投稿|"
    r"#网友投稿|#避雷|杀猪盘|跑路|被黑|维权|黑客|被盗|遭窃|遭黑客|"
    r"案件|新闻|最新消息"
)
# CTA phrases that still count as gambling ads even inside a news-like post.
TG_GAMBLE_CTA_NORMS = {
    "返水", "起步返水", "首充", "日出千万", "点击上桌", "一键试投", "点击加入游戏",
    "链上u", "自动上下分", "哈希娱乐", "注册即送", "代理加盟", "诚招代理",
    "打码量", "洗码", "娱乐群组", "usdt充提",
}

ALLOWED_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}
PACK_EXTS = {".zip", ".rar", ".7z"}
JUNK_DIR_NAMES = {"__macosx", ".git", ".svn"}
JUNK_FILE_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # single audio (admin) or one track in a pack
MAX_PACK_BYTES = 200 * 1024 * 1024
MAX_EXTRACTED_BYTES = 400 * 1024 * 1024
MAX_EXTRACTED_FILES = 200
MAX_TRACKS_PER_PACK = 50
TRACK_STATUS_PENDING = "pending"
TRACK_STATUS_APPROVED = "approved"
TRACK_STATUS_REJECTED = "rejected"

# User-facing music categories. Keys are stored on tracks.category; empty/NULL = 未分类.
TRACK_CATEGORY_CHOICES = (
    ("latest_dj", "最新DJ列表"),
    ("ktv_dj", "KTV-DJ"),
    ("note", "备注曲"),
)
TRACK_CATEGORY_LABELS = dict(TRACK_CATEGORY_CHOICES)
TRACK_CATEGORY_KEYS = {k for k, _ in TRACK_CATEGORY_CHOICES}
CHAT_LIMIT = 80
CHAT_MAX_LEN = 300
KEYWORD_MAX_LEN = 80
BLOCK_LOG_LIMIT = 200
DEFAULT_CHAT_BLOCK_KEYWORDS = (
    "广告",
    "加微信",
    "QQ",
    "联系方式",
    "赌博",
    "色情",
    "诈骗",
    "代练",
    "刷单",
    "兼职",
    "贷款",
    "投资",
    "裸聊",
    "约炮",
    "飞机",
    "TG",
    "telegram",
    "加微",
    "加V",
    "微信",
    "威信",
    "vx",
    "加q",
    "扣扣",
    "黄赌",
    "博彩",
    "彩票",
    "赌球",
    "色情网站",
    "一夜情",
    "援交",
    "嫖",
    "色情服务",
    "免费领取",
    "日入",
    "稳赚",
    "返利",
    "信用卡套现",
    "高利贷",
    "私聊",
    "飞机号",
    "t.me",
    "免费看",
    "破解",
    "外挂",
    "加QQ",
    "微信号",
    "电报",
    "黄片",
    "日赚",
    "代充",
)
PASSWORD_MIN = 8
ADMIN_PASSWORD_MIN = 12
DEFAULT_ADMIN_EMAIL = "admin@dj666.pw"
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
USERNAME_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9_]{2,20}$")
USERNAME_MIN = 2
USERNAME_MAX = 20


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def ensure_secret_key() -> str:
    load_env(ENV_FILE)
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    existing = ""
    if ENV_FILE.exists():
        existing = ENV_FILE.read_text(encoding="utf-8")
        if not existing.endswith("\n") and existing:
            existing += "\n"
    ENV_FILE.write_text(existing + f"SECRET_KEY={key}\n", encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)
    os.environ["SECRET_KEY"] = key
    return key


SECRET_KEY = ensure_secret_key()

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    session_ver: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    tracks: Mapped[list["Track"]] = relationship(back_populates="uploader")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="user")


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    media_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    links_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    tg_reactions_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tg_views: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tg_comment_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tg_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    site_views: Mapped[int] = mapped_column(Integer, default=0)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    hidden_reason: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class TgPost(Base):
    __tablename__ = "tg_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(64), index=True)
    post_id: Mapped[int] = mapped_column(Integer, index=True)
    url: Mapped[str] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text, default="")
    media_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=TG_STATUS_NEW)
    announcement_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    filter_info: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    filter_override: Mapped[bool] = mapped_column(Boolean, default=False)
    content_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    edit_title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    edit_links_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    tg_reactions_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tg_views: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tg_comment_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tg_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class TgFilterRule(Base):
    __tablename__ = "tg_filter_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(10), default="strip")  # strip | block | image
    pattern: Mapped[str] = mapped_column(String(200), default="")
    img_hash: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    sample: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    label: Mapped[str] = mapped_column(String(100), default="")
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | seed | auto | item
    weight: Mapped[int] = mapped_column(Integer, default=2)  # gamble rule score; unused for strip/block/image
    hits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(255), unique=True)
    original_name: Mapped[str] = mapped_column(String(255))
    uploader_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default=TRACK_STATUS_PENDING)
    pack_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    flac_path: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    flac_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    flac_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    flac_added_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    storage: Mapped[str] = mapped_column(String(10), default="local")
    r2_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    flac_r2_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    uploader: Mapped[Optional[User]] = relationship(back_populates="tracks")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(String(500))
    media_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Reply snapshot: kept even if the original message is deleted later.
    reply_to_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reply_user: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    reply_excerpt: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped[User] = relationship(back_populates="messages")


class ChatBlockKeyword(Base):
    __tablename__ = "chat_block_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    word: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class ChatBlockLog(Base):
    __tablename__ = "chat_block_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    content: Mapped[str] = mapped_column(String(500))
    keyword: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped[Optional[User]] = relationship()


class TgComment(Base):
    """Public comment text from the source channel's discussion (stored text only, shown as dj666)."""
    __tablename__ = "tg_comments"
    __table_args__ = (UniqueConstraint("channel", "comment_id", name="ux_tg_comment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(64))
    post_id: Mapped[int] = mapped_column(Integer, index=True)
    comment_id: Mapped[int] = mapped_column(Integer)
    author: Mapped[str] = mapped_column(String(80), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    reply_to: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class TgCommentThread(Base):
    __tablename__ = "tg_comment_threads"
    __table_args__ = (UniqueConstraint("channel", "post_id", name="ux_tg_comment_thread"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(64))
    post_id: Mapped[int] = mapped_column(Integer, index=True)
    tg_count: Mapped[int] = mapped_column(Integer, default=0)
    stored: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)


class AnnouncementLike(Base):
    __tablename__ = "announcement_likes"
    __table_args__ = (UniqueConstraint("announcement_id", "user_id", name="ux_ann_like_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    announcement_id: Mapped[int] = mapped_column(ForeignKey("announcements.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class AnnouncementComment(Base):
    __tablename__ = "announcement_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    announcement_id: Mapped[int] = mapped_column(ForeignKey("announcements.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped[User] = relationship()
    announcement: Mapped[Announcement] = relationship()


ANN_COMMENT_MAX_LEN = 300
ANN_COMMENT_SHOW = 100
ANN_COMMENT_COOLDOWN = 3


def ann_interactions(db: Session, ann_ids: list[int], user: Optional["User"], anns: Optional[list] = None) -> dict:
    """Like/comment counts, the viewer's likes and recent comments per announcement.

    likes/comments/views are display totals: synced TG numbers + real site data.
    real_likes/real_comments/site_views keep the site's own real numbers.
    """
    stats = {aid: {"likes": 0, "comments": 0, "liked": False, "items": []} for aid in ann_ids}
    if not ann_ids:
        return stats
    for aid, n in db.execute(
        select(AnnouncementLike.announcement_id, func.count())
        .where(AnnouncementLike.announcement_id.in_(ann_ids))
        .group_by(AnnouncementLike.announcement_id)
    ).all():
        stats[aid]["likes"] = n
    for aid, n in db.execute(
        select(AnnouncementComment.announcement_id, func.count())
        .where(AnnouncementComment.announcement_id.in_(ann_ids))
        .group_by(AnnouncementComment.announcement_id)
    ).all():
        stats[aid]["comments"] = n
    if user:
        for aid in db.scalars(
            select(AnnouncementLike.announcement_id).where(
                AnnouncementLike.user_id == user.id,
                AnnouncementLike.announcement_id.in_(ann_ids),
            )
        ).all():
            stats[aid]["liked"] = True
    rows = db.scalars(
        select(AnnouncementComment)
        .options(joinedload(AnnouncementComment.user))
        .where(AnnouncementComment.announcement_id.in_(ann_ids))
        .order_by(AnnouncementComment.created_at.desc(), AnnouncementComment.id.desc())
    ).unique().all()
    for c in rows:
        bucket = stats[c.announcement_id]["items"]
        if len(bucket) < ANN_COMMENT_SHOW:
            bucket.append(c)
    for v in stats.values():
        v["items"].reverse()
    by_id = {a.id: a for a in (anns or [])}
    for aid, v in stats.items():
        a = by_id.get(aid)
        tg = ann_tg_parts(a)
        v["real_likes"] = v["likes"]
        v["real_comments"] = v["comments"]
        v["tg_likes"] = tg["likes"]
        v["tg_comments"] = tg["comments"]
        v["likes"] = v["real_likes"] + tg["likes"]
        v["comments"] = v["real_comments"] + tg["comments"]
        v["site_views"] = int(getattr(a, "site_views", 0) or 0) if a is not None else 0
        v["views"] = tg["views"] + v["site_views"]
    return stats


def ann_tg_parts(obj) -> dict:
    """Split synced TG engagement: 👍 -> likes, every other reaction (incl. custom 表情) + TG comments -> comments."""
    out = {"likes": 0, "comments": 0, "views": 0}
    if obj is None:
        return out
    raw = getattr(obj, "tg_reactions_json", None)
    reactions = []
    if raw:
        try:
            reactions = json.loads(raw) or []
        except Exception:  # noqa: BLE001
            reactions = []
    for r in reactions:
        if not isinstance(r, dict):
            continue
        try:
            c = int(r.get("count") or 0)
        except (TypeError, ValueError):
            c = 0
        if c <= 0:
            continue
        emo = (r.get("emoji") or "").strip()
        if r.get("kind") != "custom" and emo.startswith("\U0001F44D"):
            out["likes"] += c
        else:
            out["comments"] += c
    try:
        out["comments"] += int(getattr(obj, "tg_comment_count", 0) or 0)
    except (TypeError, ValueError):
        pass
    try:
        out["views"] = int(getattr(obj, "tg_views", 0) or 0)
    except (TypeError, ValueError):
        pass
    return out


ANN_VIEW_BOT_RE = re.compile(r"bot|spider|crawl|slurp|curl|wget|python-requests|python-urllib|go-http|okhttp|facebookexternalhit|preview", re.I)
ANN_VIEW_IP_CAP = 20  # max counted views per IP per announcement per day (shared NAT / Wi-Fi)
_ann_view_lock = threading.Lock()
_ann_view_seen: dict = {"day": "", "vid": set(), "ip": {}}


def count_announcement_views(request: Request, db: Session, ann_ids: list[int]) -> set[int]:
    """Count one real view per visitor (session) per announcement per day; returns ids counted now."""
    if not ann_ids:
        return set()
    ua = request.headers.get("user-agent") or ""
    if not ua or ANN_VIEW_BOT_RE.search(ua):
        return set()
    vid = request.session.get("vid")
    if not vid:
        vid = secrets.token_hex(8)
        request.session["vid"] = vid
    ip = (request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "") or "?").strip()[:64]
    day = datetime.now(TZ_CN).strftime("%Y%m%d")
    fresh: list[int] = []
    with _ann_view_lock:
        if _ann_view_seen["day"] != day:
            _ann_view_seen.update(day=day, vid=set(), ip={})
        seen, ipc = _ann_view_seen["vid"], _ann_view_seen["ip"]
        for aid in ann_ids:
            k = (vid, aid)
            if k in seen:
                continue
            ik = (ip, aid)
            if ipc.get(ik, 0) >= ANN_VIEW_IP_CAP:
                continue
            seen.add(k)
            ipc[ik] = ipc.get(ik, 0) + 1
            fresh.append(aid)
    if not fresh:
        return set()
    try:
        params = {f"i{n}": aid for n, aid in enumerate(fresh)}
        # Own short transaction so the request session's loaded rows are not expired/reloaded.
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE announcements SET site_views = COALESCE(site_views, 0) + 1 WHERE id IN ("
                     + ",".join(f":i{n}" for n in range(len(fresh))) + ")"),
                params,
            )
    except Exception:  # noqa: BLE001
        return set()
    return set(fresh)


def normalize_keyword(word: str) -> str:
    return (word or "").strip()


def find_blocked_keyword(content: str, keywords: list[ChatBlockKeyword]) -> Optional[str]:
    haystack = (content or "").casefold()
    if not haystack:
        return None
    for item in keywords:
        needle = (item.word or "").strip().casefold()
        if needle and needle in haystack:
            return item.word
    return None


def seed_default_block_keywords(db: Session) -> None:
    existing = db.scalars(select(ChatBlockKeyword)).all()
    taken = {(item.word or "").strip().casefold() for item in existing if (item.word or "").strip()}
    added = False
    for raw in DEFAULT_CHAT_BLOCK_KEYWORDS:
        word = normalize_keyword(raw)
        if not word or len(word) > KEYWORD_MAX_LEN:
            continue
        folded = word.casefold()
        if folded in taken:
            continue
        db.add(ChatBlockKeyword(word=word))
        taken.add(folded)
        added = True
    if added:
        db.commit()


def display_name(user: Optional[User], fallback: str = "已注销用户") -> str:
    if not user:
        return fallback
    name = (user.username or "").strip()
    if name:
        return name
    email = user.email or ""
    return email.split("@", 1)[0] if email else fallback


def sanitize_username_seed(raw: str) -> str:
    cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9_]", "_", raw or "")
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if len(cleaned) < USERNAME_MIN:
        cleaned = f"{cleaned}user"[:USERNAME_MAX]
    if len(cleaned) < USERNAME_MIN:
        cleaned = "user"
    return cleaned[:USERNAME_MAX]


def default_username_seed(email: str, is_admin: bool = False) -> str:
    if is_admin and (email or "").strip().lower() == DEFAULT_ADMIN_EMAIL:
        return "管理员"
    local = (email or "").split("@", 1)[0]
    return sanitize_username_seed(local)


def allocate_username(
    db: Session,
    seed: str,
    exclude_id: Optional[int] = None,
    reserved: Optional[set[str]] = None,
) -> str:
    base = sanitize_username_seed(seed)
    reserved = reserved if reserved is not None else set()
    n = 0
    while True:
        candidate = base if n == 0 else f"{base[: USERNAME_MAX - len(str(n))]}{n}"
        if candidate not in reserved:
            query = select(User.id).where(User.username == candidate)
            if exclude_id is not None:
                query = query.where(User.id != exclude_id)
            if db.scalar(query) is None:
                return candidate
        n += 1


def validate_username(value: str) -> Optional[str]:
    name = (value or "").strip()
    if not USERNAME_RE.match(name):
        return "用户名需为 2–20 个字符，仅可使用中文、字母、数字和下划线。"
    return None


def migrate_schema(db: Session) -> None:
    cols = db.execute(text("PRAGMA table_info(users)")).fetchall()
    names = {row[1] for row in cols}
    if "username" not in names:
        db.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR(20)"))
        db.commit()
    if "session_ver" not in names:
        db.execute(text("ALTER TABLE users ADD COLUMN session_ver INTEGER DEFAULT 0"))
        db.commit()
    db.execute(text("UPDATE users SET session_ver = 0 WHERE session_ver IS NULL"))
    db.commit()
    taken: set[str] = set()
    users = db.scalars(select(User).order_by(User.id)).all()
    changed = False
    for user in users:
        current = (user.username or "").strip()
        if current and USERNAME_RE.match(current) and current not in taken:
            taken.add(current)
            if user.username != current:
                user.username = current
                changed = True
            continue
        seed = current if current else default_username_seed(user.email, user.is_admin)
        candidate = allocate_username(db, seed, exclude_id=user.id, reserved=taken)
        user.username = candidate
        taken.add(candidate)
        changed = True
    if changed:
        db.commit()
    db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)"))
    db.commit()
    migrate_tracks_schema(db)
    migrate_announcements_schema(db)


def migrate_announcements_schema(db: Session) -> None:
    cols = {row[1] for row in db.execute(text("PRAGMA table_info(announcements)")).fetchall()}
    if "media_json" not in cols:
        db.execute(text("ALTER TABLE announcements ADD COLUMN media_json TEXT"))
        db.commit()
    if "source_url" not in cols:
        db.execute(text("ALTER TABLE announcements ADD COLUMN source_url VARCHAR(300)"))
        db.commit()
    if "links_json" not in cols:
        db.execute(text("ALTER TABLE announcements ADD COLUMN links_json TEXT"))
        db.commit()
    cols = {row[1] for row in db.execute(text("PRAGMA table_info(announcements)")).fetchall()}
    if "content_edited" not in cols:
        db.execute(text("ALTER TABLE announcements ADD COLUMN content_edited BOOLEAN DEFAULT 0"))
        db.commit()
    db.execute(text("UPDATE announcements SET content_edited = 0 WHERE content_edited IS NULL"))
    db.commit()
    # second pass after possible adds — re-read cols below
    cols = {row[1] for row in db.execute(text("PRAGMA table_info(announcements)")).fetchall()}
    for col, ddl in (
        ("tg_reactions_json", "TEXT"),
        ("tg_views", "INTEGER"),
        ("tg_comment_count", "INTEGER"),
        ("tg_synced_at", "DATETIME"),
        ("site_views", "INTEGER DEFAULT 0"),
        ("hidden", "BOOLEAN DEFAULT 0"),
        ("hidden_at", "DATETIME"),
        ("hidden_reason", "VARCHAR(300)"),
    ):
        if col not in cols:
            db.execute(text(f"ALTER TABLE announcements ADD COLUMN {col} {ddl}"))
            db.commit()
    db.execute(text("UPDATE announcements SET hidden = 0 WHERE hidden IS NULL"))
    db.commit()
    chat_cols = {row[1] for row in db.execute(text("PRAGMA table_info(chat_messages)")).fetchall()}
    if "media_json" not in chat_cols:
        db.execute(text("ALTER TABLE chat_messages ADD COLUMN media_json TEXT"))
        db.commit()
    for col, ddl in (
        ("reply_to_id", "INTEGER"),
        ("reply_user", "VARCHAR(80)"),
        ("reply_excerpt", "VARCHAR(120)"),
    ):
        if col not in chat_cols:
            db.execute(text(f"ALTER TABLE chat_messages ADD COLUMN {col} {ddl}"))
            db.commit()
    db.execute(
        text("CREATE UNIQUE INDEX IF NOT EXISTS ux_tg_posts_channel_post ON tg_posts (channel, post_id)")
    )
    db.commit()
    tg_cols = {row[1] for row in db.execute(text("PRAGMA table_info(tg_posts)")).fetchall()}
    for col, ddl in (
        ("raw_text", "TEXT"),
        ("filter_info", "VARCHAR(300)"),
        ("filter_override", "BOOLEAN DEFAULT 0"),
        ("tg_reactions_json", "TEXT"),
        ("tg_views", "INTEGER"),
        ("tg_comment_count", "INTEGER"),
        ("tg_synced_at", "DATETIME"),
        ("content_edited", "BOOLEAN DEFAULT 0"),
        ("edit_title", "VARCHAR(200)"),
        ("edit_links_json", "TEXT"),
    ):
        if col not in tg_cols:
            db.execute(text(f"ALTER TABLE tg_posts ADD COLUMN {col} {ddl}"))
            db.commit()
    db.execute(text("UPDATE tg_posts SET filter_override = 0 WHERE filter_override IS NULL"))
    db.execute(text("UPDATE tg_posts SET content_edited = 0 WHERE content_edited IS NULL"))
    db.execute(text("UPDATE tg_posts SET raw_text = text WHERE raw_text IS NULL AND status = 'new'"))
    db.commit()
    tgf_cols = {row[1] for row in db.execute(text("PRAGMA table_info(tg_filter_rules)")).fetchall()}
    if tgf_cols and "weight" not in tgf_cols:
        db.execute(text("ALTER TABLE tg_filter_rules ADD COLUMN weight INTEGER DEFAULT 2"))
        db.commit()



def migrate_tracks_schema(db: Session) -> None:
    cols = {row[1] for row in db.execute(text("PRAGMA table_info(tracks)")).fetchall()}
    if "status" not in cols:
        db.execute(text("ALTER TABLE tracks ADD COLUMN status VARCHAR(20)"))
        db.commit()
    if "pack_name" not in cols:
        db.execute(text("ALTER TABLE tracks ADD COLUMN pack_name VARCHAR(255)"))
        db.commit()
    if "content_hash" not in cols:
        db.execute(text("ALTER TABLE tracks ADD COLUMN content_hash VARCHAR(64)"))
        db.commit()
    if "file_size" not in cols:
        db.execute(text("ALTER TABLE tracks ADD COLUMN file_size INTEGER"))
        db.commit()
    added_category = "category" not in cols
    if added_category:
        db.execute(text("ALTER TABLE tracks ADD COLUMN category VARCHAR(32)"))
        db.commit()
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_tracks_content_hash ON tracks (content_hash)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_tracks_category ON tracks (category)"))
    db.commit()
    cols = {row[1] for row in db.execute(text("PRAGMA table_info(tracks)")).fetchall()}
    for col, ddl in (
        ("flac_path", "VARCHAR(64)"),
        ("flac_size", "INTEGER"),
        ("flac_pinned", "BOOLEAN DEFAULT 0"),
        ("flac_added_at", "DATETIME"),
        ("storage", "VARCHAR(10) DEFAULT 'local'"),
        ("r2_key", "VARCHAR(200)"),
        ("flac_r2_key", "VARCHAR(200)"),
    ):
        if col not in cols:
            db.execute(text(f"ALTER TABLE tracks ADD COLUMN {col} {ddl}"))
            db.commit()
    db.execute(text("UPDATE tracks SET flac_pinned = 0 WHERE flac_pinned IS NULL"))
    db.execute(text("UPDATE tracks SET storage = 'local' WHERE storage IS NULL OR storage = ''"))
    db.commit()
    db.execute(
        text(
            "UPDATE tracks SET status = :approved "
            "WHERE status IS NULL OR status = ''"
        ),
        {"approved": TRACK_STATUS_APPROVED},
    )
    db.commit()
    if added_category:
        # One-time backfill: only assign when the title/pack clearly matches a category.
        for track in db.scalars(select(Track)).all():
            guess = infer_track_category(track.title, track.pack_name, track.original_name)
            if guess:
                track.category = guess
        db.commit()


def patch_multipart_limits() -> None:
    """Allow music-pack uploads larger than Starlette's 1MB default."""
    limit = MAX_PACK_BYTES + 1024 * 1024

    def _form(
        self,
        *,
        max_files: int | float = 1000,
        max_fields: int | float = 1000,
        max_part_size: int = 1024 * 1024,
    ):
        if max_part_size < limit:
            max_part_size = limit
        return _orig_form(
            self,
            max_files=max_files,
            max_fields=max_fields,
            max_part_size=max_part_size,
        )

    _orig_form = StarletteRequest.form
    StarletteRequest.form = _form  # type: ignore[method-assign]


patch_multipart_limits()


def is_within_dir(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def is_junk_path(rel: Path) -> bool:
    parts = [p.lower() for p in rel.parts]
    if any(part in JUNK_DIR_NAMES or part.startswith("._") for part in parts):
        return True
    name = rel.name.lower()
    if name in JUNK_FILE_NAMES or name.startswith("._") or name.startswith("."):
        return True
    return False


def find_archive_tool(ext: str) -> Optional[list[str]]:
    if ext == ".zip":
        return ["zipfile"]
    unar = shutil.which("unar")
    seven = shutil.which("7z") or shutil.which("7za")
    unrar = shutil.which("unrar")
    if ext == ".rar":
        if unar:
            return [unar]
        if unrar:
            return [unrar]
        if seven:
            return [seven]
        return None
    if ext == ".7z":
        if unar:
            return [unar]
        if seven:
            return [seven]
        return None
    return None


def extract_zip_safe(archive_path: Path, dest_dir: Path) -> None:
    dest_root = dest_dir.resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as zf:
        total = 0
        count = 0
        for info in zf.infolist():
            name = (info.filename or "").replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts:
                continue
            if is_junk_path(rel):
                continue
            size = info.file_size or 0
            total += size
            count += 1
            if count > MAX_EXTRACTED_FILES or total > MAX_EXTRACTED_BYTES:
                raise ValueError("音乐包解压后体积或文件数量过大。")
            target = (dest_root / rel).resolve()
            if not is_within_dir(target, dest_root):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, length=1024 * 1024)


def extract_with_tool(archive_path: Path, dest_dir: Path, tool: list[str]) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = str(dest_dir)
    src = str(archive_path)
    binary = Path(tool[0]).name
    if binary == "unar":
        cmd = [tool[0], "-q", "-D", "-o", dest, src]
    elif binary == "unrar":
        cmd = [tool[0], "x", "-o+", "-idq", src, dest + os.sep]
    else:
        cmd = [tool[0], "x", f"-o{dest}", "-y", "-bd", src]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("音乐包解压超时，请换一个较小的压缩包。") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        hint = detail[-1] if detail else "解压失败"
        raise ValueError(f"音乐包解压失败：{hint[:200]}")
    # Reject any extracted path that escaped the destination (symlink / traversal).
    root = dest_dir.resolve()
    for item in dest_dir.rglob("*"):
        if item.is_symlink() or not is_within_dir(item, root):
            try:
                item.unlink(missing_ok=True)
            except OSError:
                pass


def extract_archive(archive_path: Path, dest_dir: Path, ext: str) -> None:
    if ext == ".zip":
        extract_zip_safe(archive_path, dest_dir)
        return
    tool = find_archive_tool(ext)
    if not tool or tool == ["zipfile"]:
        raise ValueError("服务器暂不支持该压缩格式，请改用 ZIP 音乐包上传。")
    extract_with_tool(archive_path, dest_dir, tool)


def iter_pack_audio_files(root: Path):
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            name
            for name in dirnames
            if name.lower() not in JUNK_DIR_NAMES and not name.startswith(".")
        ]
        current = Path(dirpath)
        try:
            current_resolved = current.resolve()
        except OSError:
            continue
        if not is_within_dir(current_resolved, root):
            continue
        for name in filenames:
            path = current / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                rel = path.resolve().relative_to(root)
            except (ValueError, OSError):
                continue
            if is_junk_path(rel):
                continue
            ext = path.suffix.lower()
            if ext in ALLOWED_EXTS:
                yield path, rel, ext


def unique_upload_name(ext: str) -> str:
    return f"{uuid.uuid4().hex}{ext}"


def store_audio_file(src: Path, ext: str) -> Optional[str]:
    size = src.stat().st_size
    if size <= 0 or size > MAX_UPLOAD_BYTES:
        return None
    stored = unique_upload_name(ext)
    dest = UPLOAD_DIR / stored
    shutil.copy2(src, dest)
    dest.chmod(0o644)
    return stored


def delete_track_file(filename: str) -> None:
    if not filename or Path(filename).name != filename:
        return
    path = UPLOAD_DIR / filename
    try:
        resolved = path.resolve()
    except OSError:
        return
    if resolved.parent == UPLOAD_DIR.resolve() and resolved.is_file():
        resolved.unlink(missing_ok=True)


def r2_delete_key(key: str) -> None:
    if not key or not r2_storage.r2_enabled():
        return
    try:
        r2_storage.delete(key)
    except Exception:  # noqa: BLE001
        logging.exception("r2 delete failed")


def track_audio_url(track) -> str:
    if getattr(track, "storage", "local") == "r2" and getattr(track, "r2_key", None):
        return r2_storage.public_url(track.r2_key)
    name = Path(getattr(track, "filename", "") or "").name
    return f"/uploads/{name}" if name else ""


def track_has_flac(track) -> bool:
    return bool(dj_flac.has_flac(track))


_R2_QUEUE: "queue_mod.Queue" = queue_mod.Queue()
_R2_THREAD = None
_R2_LOCK = threading.Lock()
_R2_STATUS = {
    "state": "idle",
    "done": 0,
    "total": 0,
    "failed": 0,
    "current": "",
    "last_error": "",
    "enabled": False,
}
R2_CMD_PATH = DATA_DIR / "r2.cmd"


def r2_status_snapshot() -> dict:
    with _R2_LOCK:
        snap = dict(_R2_STATUS)
    snap["enabled"] = r2_storage.r2_enabled()
    snap["public_base"] = r2_storage.public_base() if r2_storage.r2_enabled() else ""
    return snap


def _r2_set(**kwargs) -> None:
    with _R2_LOCK:
        _R2_STATUS.update(kwargs)


def upload_track_to_r2(track_id: int) -> dict:
    """Upload a track's local MP3 (and FLAC if present) to R2, verify size, then drop local copies.

    On any failure the local files and 'local' storage flag are kept so playback still works.
    """
    if not r2_storage.r2_enabled():
        return {"ok": False, "error": "r2_disabled"}
    db = SessionLocal()
    try:
        track = db.get(Track, track_id)
        if not track:
            return {"ok": False, "error": "missing"}
        fname = Path(track.filename or "").name
        local_mp3 = UPLOAD_DIR / fname if fname else None
        already = (getattr(track, "storage", "local") == "r2" and getattr(track, "r2_key", None))
        if already and (not local_mp3 or not local_mp3.is_file()):
            # Already on R2; nothing local left to move. Still try leftover FLAC.
            if track.flac_path:
                flac_path = dj_flac.resolve_flac(UPLOAD_DIR, track.flac_path)
                if flac_path and flac_path.is_file():
                    pass  # fall through to flac-only path below via a flag
                else:
                    return {"ok": True, "skipped": "already_r2"}
            else:
                return {"ok": True, "skipped": "already_r2"}
        if not local_mp3 or not local_mp3.is_file():
            if already:
                return {"ok": True, "skipped": "already_r2"}
            logging.error("r2 upload skipped, local mp3 missing track=%s", track_id)
            return {"ok": False, "error": "no_local"}
        key = track.r2_key or r2_storage.mp3_key(track.id, track.title or "")
        local_size = int(local_mp3.stat().st_size)
        r2_storage.upload_file(local_mp3, key, content_type="audio/mpeg")
        remote = r2_storage.object_size(key)
        if remote != local_size:
            logging.error("r2 mp3 size mismatch track=%s local=%s remote=%s", track_id, local_size, remote)
            return {"ok": False, "error": "size_mismatch"}
        flac_key = getattr(track, "flac_r2_key", None)
        if track.flac_path:
            flac_path = dj_flac.resolve_flac(UPLOAD_DIR, track.flac_path)
            if flac_path and flac_path.is_file():
                flac_key = flac_key or r2_storage.flac_key(track.id)
                r2_storage.upload_file(
                    flac_path, flac_key, content_type="audio/flac",
                    cache_control="private, max-age=0, no-store",
                )
                fr = r2_storage.object_size(flac_key)
                if fr != int(flac_path.stat().st_size):
                    logging.error("r2 flac size mismatch track=%s", track_id)
                    return {"ok": False, "error": "flac_size_mismatch"}
        track.storage = "r2"
        track.r2_key = key
        if flac_key:
            track.flac_r2_key = flac_key
        if local_size and not track.file_size:
            track.file_size = local_size
        db.commit()
        try:
            delete_track_file(fname)
            if track.flac_path:
                path = dj_flac.resolve_flac(UPLOAD_DIR, track.flac_path)
                if path:
                    dj_flac.unlink_with_snapshots(path, FLAC_SNAP_ROOTS)
                track.flac_path = None
                db.commit()
        except Exception:  # noqa: BLE001
            logging.exception("r2 post-upload local delete failed track=%s", track_id)
        logging.info("r2 upload ok track=%s", track_id)
        return {"ok": True}
    except Exception:  # noqa: BLE001
        db.rollback()
        logging.exception("r2 upload failed track=%s", track_id)
        return {"ok": False, "error": "upload_failed"}
    finally:
        db.close()


def schedule_r2_upload(track_id) -> None:
    if not r2_storage.r2_enabled():
        return
    try:
        tid = int(track_id)
    except (TypeError, ValueError):
        return
    if tid > 0:
        _R2_QUEUE.put(("upload", tid))


def _r2_pending_ids(db) -> list[int]:
    rows = db.scalars(select(Track).order_by(Track.id)).all()
    out = []
    for t in rows:
        if (getattr(t, "storage", "local") or "local") != "r2":
            out.append(t.id)
        elif t.flac_path and dj_flac.resolve_flac(UPLOAD_DIR, t.flac_path):
            out.append(t.id)
    return out


def _r2_run_migration() -> None:
    if not r2_storage.r2_enabled():
        _r2_set(state="idle", last_error="r2_disabled")
        return
    db = SessionLocal()
    try:
        ids = _r2_pending_ids(db)
    finally:
        db.close()
    _r2_set(state="running", done=0, total=len(ids), failed=0, current="", last_error="")
    failed = 0
    done = 0
    last_err = ""
    for tid in ids:
        title = ""
        db = SessionLocal()
        try:
            tr = db.get(Track, tid)
            title = (tr.title if tr else "") or f"#{tid}"
        finally:
            db.close()
        _r2_set(current=f"#{tid} {title}")
        result = upload_track_to_r2(tid)
        done += 1
        if not result.get("ok"):
            failed += 1
            last_err = f"#{tid}: {result.get('error') or 'failed'}"
            logging.error("r2 migrate failed track=%s err=%s", tid, result.get("error"))
        _r2_set(done=done, failed=failed, last_error=last_err)
    _r2_set(state="done", current="", done=done, failed=failed, last_error=last_err)
    logging.info("r2 migrate finished done=%s failed=%s", done, failed)


def _r2_check_cmdfile() -> None:
    path = R2_CMD_PATH
    if not path.is_file():
        return
    try:
        raw = path.read_text(encoding="utf-8").strip()
        path.unlink(missing_ok=True)
    except OSError:
        return
    if not raw:
        return
    parts = raw.split()
    cmd = parts[0].lower()
    if cmd == "migrate":
        with _R2_LOCK:
            if _R2_STATUS.get("state") == "running":
                return
        _R2_QUEUE.put(("migrate",))
    elif cmd == "upload" and len(parts) >= 2:
        try:
            _R2_QUEUE.put(("upload", int(parts[1])))
        except ValueError:
            logging.error("r2 cmd ignored: bad track id")


def _r2_worker() -> None:
    logging.info("r2 worker started enabled=%s", r2_storage.r2_enabled())
    while True:
        try:
            try:
                job = _R2_QUEUE.get(timeout=1)
            except queue_mod.Empty:
                try:
                    _r2_check_cmdfile()
                except Exception:  # noqa: BLE001
                    logging.exception("r2 cmdfile check failed")
                continue
            try:
                if job[0] == "upload":
                    upload_track_to_r2(job[1])
                elif job[0] == "migrate":
                    _r2_run_migration()
            finally:
                _R2_QUEUE.task_done()
        except Exception:  # noqa: BLE001
            logging.exception("r2 worker loop error")


def start_r2_worker() -> None:
    global _R2_THREAD
    if _R2_THREAD is not None and _R2_THREAD.is_alive():
        return
    t = threading.Thread(target=_r2_worker, name="r2-upload", daemon=True)
    _R2_THREAD = t
    t.start()


def approved_tracks(db: Session):
    return (
        db.scalars(
            select(Track)
            .options(joinedload(Track.uploader))
            .where(Track.status == TRACK_STATUS_APPROVED)
            .order_by(Track.created_at.desc())
        )
        .unique()
        .all()
    )


async def save_upload_limited(file: UploadFile, dest: Path, max_bytes: int) -> int:
    size = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    out.close()
                    dest.unlink(missing_ok=True)
                    return -1
                out.write(chunk)
    finally:
        await file.close()
    return size


async def save_upload_hashed(file: UploadFile, dest: Path, max_bytes: int) -> tuple[int, str]:
    """Like save_upload_limited, but also returns the SHA-256 of the bytes."""
    size = 0
    digest = hashlib.sha256()
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    out.close()
                    dest.unlink(missing_ok=True)
                    return -1, ""
                digest.update(chunk)
                out.write(chunk)
    finally:
        await file.close()
    return size, digest.hexdigest()


# ---------------------------------------------------------------- upload dedup
DUP_STAGING_DIR = DATA_DIR / "dup_staging"
DUP_STAGING_TTL = timedelta(hours=24)
DUP_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
TRACK_STATUS_LABELS = {
    TRACK_STATUS_APPROVED: "已发布",
    TRACK_STATUS_PENDING: "待审核",
    TRACK_STATUS_REJECTED: "已拒绝",
}


def normalize_track_category(value: Optional[str]) -> Optional[str]:
    v = (value or "").strip()
    if not v:
        return None
    if v in TRACK_CATEGORY_KEYS:
        return v
    for key, label in TRACK_CATEGORY_CHOICES:
        if v == label:
            return key
    return None


def infer_track_category(
    title: str = "",
    pack_name: Optional[str] = "",
    original_name: Optional[str] = "",
) -> Optional[str]:
    """Assign a category only when the name clearly points to one of the three labels."""
    parts = " ".join(
        p for p in ((title or ""), (pack_name or ""), (original_name or "")) if p
    )
    if not parts.strip():
        return None
    blob = (
        parts.lower()
        .replace("_", " ")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
    )
    blob = re.sub(r"\s+", " ", blob)
    if "ktv" in blob:
        return "ktv_dj"
    if "备注曲" in parts:
        return "note"
    if "最新dj列表" in blob:
        return "latest_dj"
    return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backfill_track_hashes(db: Session) -> int:
    """Compute SHA-256/size for tracks stored before dedup existed."""
    rows = db.scalars(
        select(Track).where(or_(Track.content_hash.is_(None), Track.file_size.is_(None)))
    ).all()
    done = 0
    for track in rows:
        path = UPLOAD_DIR / Path(track.filename or "").name
        try:
            if not track.filename or not path.is_file():
                continue
            track.file_size = path.stat().st_size
            track.content_hash = file_sha256(path)
            done += 1
        except OSError:
            continue
    if done:
        db.commit()
    return done


def find_duplicate_tracks(db: Session, sha: str, size: int, original: str) -> list[dict]:
    """Existing tracks (any status) that look like the same audio.

    Primary check: identical SHA-256 of the file bytes (catches renamed copies).
    Secondary: same original filename (case-insensitive) AND same byte size.
    """
    backfill_track_hashes(db)
    name = (original or "").strip().lower()
    conds = [Track.content_hash == sha]
    if name and size > 0:
        conds.append(and_(func.lower(Track.original_name) == name, Track.file_size == size))
    rows = (
        db.scalars(
            select(Track).options(joinedload(Track.uploader)).where(or_(*conds)).order_by(Track.created_at.desc())
        )
        .unique()
        .all()
    )
    out = []
    for t in rows:
        out.append(
            {
                "id": t.id,
                "title": t.title,
                "original_name": t.original_name or "",
                "status": t.status,
                "status_label": TRACK_STATUS_LABELS.get(t.status, t.status or ""),
                "uploader_id": t.uploader_id,
                "uploader": display_name(t.uploader, "未知"),
                "reason": "内容完全相同" if t.content_hash == sha else "同名且大小相同",
                "same_content": t.content_hash == sha,
            }
        )
    out.sort(key=lambda m: 0 if m["same_content"] else 1)
    return out


def overwrite_track_audio(
    db: Session, track: Track, stored_name: str, original: str, size: int, sha: str, by_user: User
) -> None:
    """Point an existing track row at a newly stored audio file (keeps title/row)."""
    old_file = track.filename
    content_changed = track.content_hash != sha
    track.filename = stored_name
    track.original_name = (original or track.original_name or "")[:255]
    track.file_size = size
    track.content_hash = sha
    # A normal user replacing the audio of an already reviewed track must go
    # through review again; admins keep the current status.
    if content_changed and not by_user.is_admin and track.status != TRACK_STATUS_PENDING:
        track.status = TRACK_STATUS_PENDING
    db.commit()
    if old_file and old_file != stored_name:
        delete_track_file(old_file)
    if getattr(track, "storage", "local") == "r2" or getattr(track, "r2_key", None):
        track.storage = "local"
        db.commit()
    schedule_r2_upload(track.id)


def can_overwrite_track(user: User, match: dict) -> bool:
    return bool(user.is_admin or (match.get("uploader_id") and match["uploader_id"] == user.id))


def cleanup_dup_staging() -> None:
    if not DUP_STAGING_DIR.is_dir():
        return
    cutoff = datetime.now().timestamp() - DUP_STAGING_TTL.total_seconds()
    for d in DUP_STAGING_DIR.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            continue


def load_dup_batches(user: User) -> list[dict]:
    cleanup_dup_staging()
    batches = []
    if not DUP_STAGING_DIR.is_dir():
        return batches
    for d in DUP_STAGING_DIR.iterdir():
        mf = d / "manifest.json"
        if not d.is_dir() or not mf.is_file():
            continue
        try:
            data = json.loads(mf.read_text("utf-8"))
        except Exception:
            continue
        if data.get("user_id") != user.id:
            continue
        data["token"] = d.name
        for item in data.get("items", []):
            for m in item.get("matches", []):
                m["can_overwrite"] = can_overwrite_track(user, m)
            item["can_overwrite"] = any(m["can_overwrite"] for m in item.get("matches", []))
        batches.append(data)
    batches.sort(key=lambda b: b.get("created", 0))
    return batches


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode(
        "utf-8"
    )


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def fmt_time(value: Optional[datetime]) -> str:
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TZ_CN).strftime("%Y-%m-%d %H:%M")


def fmt_date(value: Optional[datetime]) -> str:
    """Calendar date in Asia/Kuala_Lumpur (UTC+8), matching on-site 东南亚DJ时间."""
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TZ_CN).strftime("%Y-%m-%d")


CHAT_RECALL_SECS = 120  # regular users may recall their own message within 2 minutes


def chat_created_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Treat naive datetimes as UTC, matching fmt_time / site clock."""
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def chat_created_ms(value: Optional[datetime]) -> int:
    """Unix epoch milliseconds (UTC) for the public-chat recall timer."""
    dt = chat_created_utc(value)
    if not dt:
        return 0
    return int(dt.timestamp() * 1000)


def chat_recall_remaining(msg: "ChatMessage", now: Optional[datetime] = None) -> float:
    """Seconds left in the recall window; 0 if expired or missing timestamp."""
    created = chat_created_utc(getattr(msg, "created_at", None))
    if not created:
        return 0.0
    now = now or datetime.now(timezone.utc)
    return max(0.0, CHAT_RECALL_SECS - (now - created).total_seconds())


def chat_can_delete(user: Optional["User"], msg: "ChatMessage") -> tuple[bool, str]:
    """Admins may delete any message at any time; others only their own within 2 minutes."""
    if not user:
        return False, "请先登录"
    if user.is_admin:
        return True, ""
    if msg.user_id != user.id:
        return False, "只能撤回自己发送的消息。"
    if chat_recall_remaining(msg) <= 0:
        return False, "已超过2分钟，无法撤回这条消息。"
    return True, ""


# ---------------------------------------------------------------------------
# Announcement media (Telegram-style posts)
# ---------------------------------------------------------------------------


def ann_media_type(name: str) -> Optional[str]:
    ext = Path(name or "").suffix.lower()
    if ext in ANN_IMAGE_EXTS:
        return "image"
    if ext in ANN_VIDEO_EXTS:
        return "video"
    return None


def ann_media_url(name: str) -> str:
    return f"/media/ann/{name}"


def parse_media_json(raw: Optional[str]) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    items = []
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("file") or "")
        if not ANN_MEDIA_NAME_RE.match(name):
            continue
        kind = ann_media_type(name)
        if not kind:
            continue
        item = {"file": name, "type": kind, "url": ann_media_url(name)}
        poster = str(entry.get("poster") or "")
        if poster and ANN_MEDIA_NAME_RE.match(poster) and ann_media_type(poster) == "image":
            item["poster"] = ann_media_url(poster)
            item["poster_file"] = poster
        items.append(item)
    return items[:ANN_MAX_MEDIA]


def dump_media(items: list[dict]) -> Optional[str]:
    clean = []
    for item in items[:ANN_MAX_MEDIA]:
        entry = {"file": item["file"], "type": item["type"]}
        if item.get("poster_file"):
            entry["poster"] = item["poster_file"]
        clean.append(entry)
    return json.dumps(clean) if clean else None


ANN_MAX_LINKS = 10
ANN_LINK_TEXT_MAX = 80
ANN_LINK_URL_MAX = 500


def normalize_link_url(raw: str) -> Optional[str]:
    """Return a safe absolute http(s) URL, or None. Bare domains get https://."""
    from urllib.parse import urlsplit

    value = (raw or "").strip()
    if not value or len(value) > ANN_LINK_URL_MAX or any(ch.isspace() or ord(ch) < 32 for ch in value):
        return None
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
        if value.startswith("//") or not re.match(r"^[A-Za-z0-9\u00a1-\uffff-]+(\.[A-Za-z0-9\u00a1-\uffff-]+)+(:\d+)?([/?#].*)?$", value):
            return None
        value = "https://" + value
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc or "@" in parts.netloc:
        return None
    if any(c in value for c in "<>\"'`\\"):
        return None
    return value


def link_display(url: str) -> str:
    label = re.sub(r"^https?://", "", url, flags=re.I)
    label = re.sub(r"^www\.", "", label, flags=re.I).rstrip("/")
    return label if len(label) <= 60 else label[:57] + "…"


def parse_ann_links_form(form) -> tuple[list[dict], Optional[str]]:
    texts = [str(v) for v in form.getlist("link_text")]
    urls = [str(v) for v in form.getlist("link_url")]
    out: list[dict] = []
    for i in range(max(len(texts), len(urls))):
        label = (texts[i] if i < len(texts) else "").strip()
        raw = (urls[i] if i < len(urls) else "").strip()
        if not label and not raw:
            continue
        if not raw:
            return [], f"第 {i + 1} 行文案「{label[:20]}」缺少链接。"
        url = normalize_link_url(raw)
        if not url:
            return [], f"第 {i + 1} 行链接无效，只支持 http:// 或 https:// 开头的网址。"
        out.append({"text": label[:ANN_LINK_TEXT_MAX], "url": url})
    if len(out) > ANN_MAX_LINKS:
        return [], f"每条公告最多 {ANN_MAX_LINKS} 行文案链接。"
    return out, None


def ann_links(ann) -> list[dict]:
    raw = getattr(ann, "links_json", None)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        url = normalize_link_url(str(item.get("url") or ""))
        if not url:
            continue
        text_ = str(item.get("text") or "").strip()[:ANN_LINK_TEXT_MAX]
        out.append({"text": text_, "url": url, "label": text_ or link_display(url)})
    return out[:ANN_MAX_LINKS]


def dump_links(items: list[dict]) -> Optional[str]:
    return json.dumps([{"text": i["text"], "url": i["url"]} for i in items], ensure_ascii=False) if items else None


def ann_media(ann) -> list[dict]:
    return parse_media_json(getattr(ann, "media_json", None))


def chat_media(msg) -> list[dict]:
    items = parse_media_json(getattr(msg, "media_json", None))
    for item in items:
        item["url"] = f"/media/chat/{item['file']}"
    return items


CHAT_REPLY_EXCERPT_LEN = 60


def chat_excerpt(msg) -> str:
    text_ = re.sub(r"\s+", " ", (msg.content or "")).strip()
    if text_:
        return text_[:CHAT_REPLY_EXCERPT_LEN] + ("…" if len(text_) > CHAT_REPLY_EXCERPT_LEN else "")
    types = {m["type"] for m in parse_media_json(getattr(msg, "media_json", None))}
    if types == {"image"}:
        return "[图片]"
    if types == {"video"}:
        return "[视频]"
    return "[图片/视频]" if types else ""


def chat_reply_info(db: Session, messages) -> dict[int, dict]:
    """Quote data for reply messages; marks quotes whose original is gone."""
    ids = {m.reply_to_id for m in messages if getattr(m, "reply_to_id", None)}
    alive: dict[int, datetime] = {}
    if ids:
        for mid, created in db.execute(
            select(ChatMessage.id, ChatMessage.created_at).where(ChatMessage.id.in_(ids))
        ).all():
            alive[mid] = created
    info: dict[int, dict] = {}
    for m in messages:
        rid = getattr(m, "reply_to_id", None)
        if not rid:
            continue
        orig = alive.get(rid)
        exists = orig is not None and (m.created_at is None or orig is None or orig <= m.created_at)
        info[m.id] = {
            "id": rid if exists else None,
            "user": m.reply_user or "已注销用户",
            "excerpt": (m.reply_excerpt or "") if exists else "",
            "deleted": not exists,
        }
    return info


def referenced_chat_files(db: Session) -> set[str]:
    used: set[str] = set()
    for raw in db.scalars(select(ChatMessage.media_json).where(ChatMessage.media_json.is_not(None))).all():
        for item in parse_media_json(raw):
            used.add(item["file"])
    return used


def delete_chat_media_files(db: Session, names: list[str]) -> None:
    if not names:
        return
    used = referenced_chat_files(db)
    for name in names:
        if name in used or not ANN_MEDIA_NAME_RE.match(name or ""):
            continue
        (CHAT_MEDIA_DIR / name).unlink(missing_ok=True)


def referenced_media_files(db: Session) -> set[str]:
    used: set[str] = set()
    for raw in db.scalars(select(Announcement.media_json)).all():
        for item in parse_media_json(raw):
            used.add(item["file"])
            if item.get("poster_file"):
                used.add(item["poster_file"])
    for raw in db.scalars(select(TgPost.media_json)).all():
        for item in parse_media_json(raw):
            used.add(item["file"])
            if item.get("poster_file"):
                used.add(item["poster_file"])
    return used


def delete_unreferenced_media(db: Session, names: list[str]) -> None:
    if not names:
        return
    used = referenced_media_files(db)
    for name in names:
        if name in used or not ANN_MEDIA_NAME_RE.match(name or ""):
            continue
        (ANN_MEDIA_DIR / name).unlink(missing_ok=True)


def media_names(items: list[dict]) -> list[str]:
    out = []
    for item in items:
        out.append(item["file"])
        if item.get("poster_file"):
            out.append(item["poster_file"])
    return out


def cleanup_orphan_media(max_age_hours: int = 24) -> int:
    removed = 0
    db = SessionLocal()
    try:
        used_ann = referenced_media_files(db)
        used_chat = referenced_chat_files(db)
    finally:
        db.close()
    removed += _cleanup_dir(ANN_MEDIA_DIR, used_ann, max_age_hours)
    removed += _cleanup_dir(CHAT_MEDIA_DIR, used_chat, max_age_hours)
    return removed


def _cleanup_dir(folder: Path, used: set[str], max_age_hours: int) -> int:
    if not folder.is_dir():
        return 0
    cutoff = datetime.now().timestamp() - max_age_hours * 3600
    removed = 0
    for path in folder.iterdir():
        if path.name.startswith(".") and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            continue
        if not path.is_file() or not ANN_MEDIA_NAME_RE.match(path.name):
            continue
        if path.name in used:
            continue
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def sniff_media_ok(path: Path, ext: str) -> bool:
    try:
        with path.open("rb") as fh:
            head = fh.read(32)
    except OSError:
        return False
    if ext in {".jpg", ".jpeg"}:
        return head[:3] == b"\xff\xd8\xff"
    if ext == ".png":
        return head[:8] == b"\x89PNG\r\n\x1a\n"
    if ext == ".gif":
        return head[:6] in (b"GIF87a", b"GIF89a")
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext in {".mp4", ".mov", ".m4v"}:
        return b"ftyp" in head[:12] or head[4:8] in (b"moov", b"mdat", b"wide", b"free", b"skip")
    if ext == ".webm":
        return head[:4] == b"\x1a\x45\xdf\xa3"
    return False


URL_RE = re.compile(r"(https?://[^\s<>\"'）)】，。]+)")


def linkify(value: Optional[str]) -> Markup:
    escaped = str(escape(value or ""))

    def _sub(match: re.Match) -> str:
        url = match.group(1)
        return f'<a href="{url}" target="_blank" rel="noopener nofollow ugc">{url}</a>'

    return Markup(URL_RE.sub(_sub, escaped))


# ---------------------------------------------------------------------------
# Telegram public channel collection (https://t.me/s/<channel>)
# ---------------------------------------------------------------------------

_tg_lock = threading.Lock()
_tg_state: dict = {"last_run": None, "last_result": "", "running": False}

BG_URL_RE = re.compile(r"background-image:url\('([^']+)'\)")


class _TgPreviewParser(HTMLParser):
    """Parse the public t.me/s/<channel> preview into posts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.posts: list[dict] = []
        self.cur: Optional[dict] = None
        self.depth = 0
        self.text_depth: Optional[int] = None
        self.skip_depth: Optional[int] = None
        self.video_wrap_depth: Optional[int] = None
        self.pending_poster: Optional[str] = None

    @staticmethod
    def _cls(attrs: dict) -> set[str]:
        return set((attrs.get("class") or "").split())

    def handle_starttag(self, tag, attrs_list):
        attrs = dict(attrs_list)
        cls = self._cls(attrs)
        void = tag in {"br", "img", "input", "meta", "link", "source", "hr"}
        if not void:
            self.depth += 1
        if "tgme_widget_message" in cls and attrs.get("data-post"):
            self.cur = {
                "data_post": attrs["data-post"],
                "text_parts": [],
                "media": [],
                "notes": [],
                "date": None,
                "depth": self.depth,
                "buttons": [],
                "_links": [],
            }
            self.btn_depth = None
            self.posts.append(self.cur)
            return
        if self.cur is None:
            return
        if self.text_depth is not None:
            if tag == "br":
                self.cur["text_parts"].append("\n")
            elif tag == "a" and attrs.get("href"):
                self.cur.setdefault("_links", []).append(attrs.get("href"))
            return
        if "tgme_widget_message_text" in cls and "js-message_reply_text" not in cls:
            if not self.cur["text_parts"]:
                self.text_depth = self.depth
            return
        style = attrs.get("style") or ""
        if "tgme_widget_message_photo_wrap" in cls:
            match = BG_URL_RE.search(style)
            if match:
                self.cur["media"].append({"type": "image", "src": match.group(1)})
        elif "tgme_widget_message_video_player" in cls:
            self.pending_poster = None
            self.video_too_big = "not_supported" in cls
            if self.video_too_big:
                self.cur["notes"].append("含超大视频（Telegram 预览页不提供下载），已保留封面图")
        elif "tgme_widget_message_video_thumb" in cls:
            match = BG_URL_RE.search(style)
            if match:
                if getattr(self, "video_too_big", False):
                    self.cur["media"].append({"type": "image", "src": match.group(1), "fallback": True})
                    self.video_too_big = False
                else:
                    self.pending_poster = match.group(1)
        elif "tgme_widget_message_video_wrap" in cls:
            self.video_wrap_depth = self.depth
        elif tag == "video" and self.video_wrap_depth is not None and attrs.get("src"):
            self.cur["media"].append(
                {"type": "video", "src": attrs["src"], "poster": self.pending_poster}
            )
            self.pending_poster = None
        elif tag == "time" and attrs.get("datetime") and self.cur.get("date") is None:
            self.cur["date"] = attrs.get("datetime")
        elif "tgme_widget_message_inline_btn" in cls and attrs.get("href"):
            self.cur.setdefault("buttons", []).append(
                {"url": attrs.get("href") or "", "text": ""}
            )
            self.btn_depth = self.depth

    def handle_endtag(self, tag):
        if tag in {"br", "img", "input", "meta", "link", "source", "hr"}:
            return
        if self.text_depth is not None and self.depth == self.text_depth:
            self.text_depth = None
        if self.video_wrap_depth is not None and self.depth == self.video_wrap_depth:
            self.video_wrap_depth = None
        if getattr(self, "btn_depth", None) is not None and self.depth == self.btn_depth:
            self.btn_depth = None
        self.depth -= 1

    def handle_data(self, data):
        if self.cur is not None and getattr(self, "btn_depth", None) is not None:
            btns = self.cur.get("buttons") or []
            if btns:
                btns[-1]["text"] = (btns[-1].get("text") or "") + data
        if self.cur is not None and self.text_depth is not None:
            self.cur["text_parts"].append(data)


def parse_tg_preview(html_text: str) -> list[dict]:
    parser = _TgPreviewParser()
    parser.feed(html_text)
    parser.close()
    posts = []
    for raw in parser.posts:
        data_post = raw["data_post"]
        try:
            channel, pid = data_post.rsplit("/", 1)
            pid_int = int(pid)
        except ValueError:
            continue
        txt = "".join(raw["text_parts"])
        txt = re.sub(r"[ \t]+\n", "\n", txt)
        txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
        extra = tgf3.collect_extra_text(links=raw.get("_links"), buttons=raw.get("buttons"))
        if extra:
            # Keep button labels and hrefs in the stored text so filters see them.
            missing = "\n".join(
                line for line in extra.splitlines() if line and line not in txt
            )
            if missing:
                txt = (txt + "\n" + missing).strip()
        posted = None
        if raw.get("date"):
            try:
                posted = datetime.fromisoformat(raw["date"])
            except ValueError:
                posted = None
        seen = set()
        media = []
        for m in raw["media"]:
            if m["src"] in seen:
                continue
            seen.add(m["src"])
            media.append(m)
        posts.append(
            {
                "channel": channel,
                "post_id": pid_int,
                "url": f"https://t.me/{channel}/{pid_int}",
                "text": txt,
                "media": media[:ANN_MAX_MEDIA],
                "notes": list(dict.fromkeys(raw["notes"])),
                "posted_at": posted,
            }
        )
    eng = parse_tg_engagement_map(html_text)
    for post in posts:
        extra = eng.get(post["post_id"]) or {}
        post["views"] = extra.get("views")
        post["views_text"] = extra.get("views_text")
        post["reactions"] = extra.get("reactions") or []
        post["comment_count"] = extra.get("comment_count")
    return posts



def parse_tg_views_text(text: str) -> Optional[int]:
    t = (text or "").strip().upper().replace(",", "").replace(" ", "")
    m = re.match(r"^([0-9]*\.?[0-9]+)([KMB])?$", t)
    if not m:
        return None
    n = float(m.group(1))
    mul = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[m.group(2) or ""]
    return int(round(n * mul))


def parse_tg_engagement_map(html_text: str) -> dict[int, dict]:
    """Reactions and views from the public t.me/s preview. Comment bodies are not exposed."""
    out: dict[int, dict] = {}
    parts = re.split(r'(?=<div[^>]*class="[^"]*tgme_widget_message_wrap)', html_text)
    if len(parts) <= 1:
        parts = re.split(r"(?=<div[^>]*js-widget_message_wrap)", html_text)
    for ch in parts:
        m = re.search(r'data-post="[^"/]+/(\d+)"', ch)
        if not m:
            continue
        pid = int(m.group(1))
        views_t = None
        vm = re.search(r'class="tgme_widget_message_views">([^<]+)', ch)
        if vm:
            views_t = vm.group(1).strip()
        reactions: list[dict] = []
        rb = re.search(r'class="tgme_widget_message_reactions[^"]*">(.*?)</div>', ch, re.S)
        if rb:
            for cls, inner in re.findall(
                r'<span class="tgme_reaction([^"]*)">(.*?)</span>', rb.group(1), re.S
            ):
                paid = "tgme_reaction_paid" in cls
                bm = re.search(r"<b>([^<]+)</b>", inner)
                emoji = (bm.group(1).strip() if bm else "")
                text_only = re.sub(r"<[^>]+>", "", inner).strip()
                cm = re.search(r"(\d+)\s*$", text_only)
                count = int(cm.group(1)) if cm else 0
                if count <= 0:
                    continue
                if paid:
                    reactions.append({"emoji": "⭐", "count": count, "kind": "paid"})
                elif emoji:
                    reactions.append({"emoji": emoji, "count": count, "kind": "unicode"})
                else:
                    reactions.append({"emoji": "💠", "count": count, "kind": "custom"})
        comments = None
        rm = re.search(r'class="tgme_widget_message_replies[^"]*"[^>]*>.*?(\d+)', ch, re.S)
        if rm:
            comments = int(rm.group(1))
        else:
            cm2 = re.search(r"(\d+)\s*comments?", ch, re.I)
            if cm2:
                comments = int(cm2.group(1))
        out[pid] = {
            "views": parse_tg_views_text(views_t) if views_t else None,
            "views_text": views_t,
            "reactions": reactions,
            "comment_count": comments,
        }
    return out


def format_cn_count(n: int) -> str:
    if n >= 10_000:
        s = f"{n / 10_000:.1f}".rstrip("0").rstrip(".")
        return f"{s}万"
    return str(n)


def tg_eng_line(obj) -> str:
    """Compact TG engagement line. Separate from site-native likes/comments."""
    if obj is None:
        return ""
    raw = getattr(obj, "tg_reactions_json", None) if not isinstance(obj, dict) else obj.get("tg_reactions_json")
    views = getattr(obj, "tg_views", None) if not isinstance(obj, dict) else obj.get("tg_views")
    comments = getattr(obj, "tg_comment_count", None) if not isinstance(obj, dict) else obj.get("tg_comment_count")
    reactions = []
    if raw:
        try:
            reactions = json.loads(raw) or []
        except Exception:
            reactions = []
    merged: dict[str, int] = {}
    custom = 0
    for r in reactions:
        if not isinstance(r, dict):
            continue
        c = int(r.get("count") or 0)
        if c <= 0:
            continue
        if r.get("kind") == "custom":
            custom += c
            continue
        key = (r.get("emoji") or "").strip() or "💠"
        merged[key] = merged.get(key, 0) + c
    parts = [f"{emo} {format_cn_count(c)}" for emo, c in merged.items()]
    if custom:
        parts.append(f"表情 {format_cn_count(custom)}")
    if comments:
        parts.append(f"💬 {format_cn_count(int(comments))}")
    if views:
        parts.append(f"浏览 {format_cn_count(int(views))}")
    return ("TG " + " · ".join(parts)) if parts else ""


def apply_engagement_fields(row, post: dict, now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    row.tg_reactions_json = json.dumps(post.get("reactions") or [], ensure_ascii=False)
    row.tg_views = post.get("views")
    if post.get("comment_count") is not None:
        row.tg_comment_count = post.get("comment_count")
    row.tg_synced_at = now


def apply_tg_engagement(db: Session, post: dict) -> bool:
    """Write public-preview engagement onto the TgPost and linked announcement."""
    channel = post.get("channel") or TG_CHANNEL
    row = db.scalar(
        select(TgPost).where(TgPost.channel == channel, TgPost.post_id == post["post_id"])
    )
    if not row:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    apply_engagement_fields(row, post, now)
    ann = None
    if row.announcement_id:
        ann = db.get(Announcement, row.announcement_id)
    if ann is None and row.url:
        ann = db.scalar(select(Announcement).where(Announcement.source_url == row.url).limit(1))
        if ann:
            row.announcement_id = ann.id
    if ann is not None:
        apply_engagement_fields(ann, post, now)
    return True

def _http_get(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": TG_USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    return urllib.request.urlopen(req, timeout=timeout)


def download_tg_media(src: str, kind: str, max_bytes: Optional[int] = None) -> Optional[str]:
    if not src.startswith("https://"):
        return None
    ANN_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    limit = max_bytes if max_bytes is not None else (TG_VIDEO_MAX_BYTES if kind == "video" else 30 * 1024 * 1024)
    tmp = ANN_MEDIA_DIR / f".tg-{uuid.uuid4().hex}.part"
    try:
        with _http_get(src, timeout=60) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            try:
                cl = int(resp.headers.get("Content-Length") or 0)
            except ValueError:
                cl = 0
            if cl and cl > limit:
                raise ValueError("too large")
            size = 0
            with tmp.open("wb") as out:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("too large")
                    out.write(chunk)
        if size == 0:
            raise ValueError("empty")
        if kind == "video":
            ext = ".webm" if "webm" in ctype else ".mp4"
        else:
            ext = {"image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}.get(ctype.split(";")[0], ".jpg")
            if not sniff_media_ok(tmp, ext):
                for cand in (".jpg", ".png", ".webp", ".gif"):
                    if sniff_media_ok(tmp, cand):
                        ext = cand
                        break
        if not sniff_media_ok(tmp, ext):
            raise ValueError("bad media")
        name = f"{uuid.uuid4().hex}{ext}"
        dest = ANN_MEDIA_DIR / name
        tmp.rename(dest)
        dest.chmod(0o644)
        return name
    except Exception:
        tmp.unlink(missing_ok=True)
        return None


CONTACT_KEYS = ("contact_name", "contact_telegram", "contact_email", "contact_note")
TG_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
CONTACT_EMAIL_RE = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")


def normalize_telegram(raw: str) -> tuple[str, Optional[str]]:
    """Return (stored_value, error). Stored as '@username' or an https://t.me/ URL."""
    value = (raw or "").strip()
    if not value:
        return "", None
    m = re.match(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(.+)$", value, re.I)
    if m:
        path = m.group(1).strip().strip("/")
        if TG_USERNAME_RE.match(path):
            return "@" + path, None
        if re.match(r"^[A-Za-z0-9_+/\-]{2,100}$", path):
            return "https://t.me/" + path, None
        return "", "Telegram 链接格式不正确。"
    if value.startswith("@"):
        value = value[1:]
    if TG_USERNAME_RE.match(value):
        return "@" + value, None
    return "", "Telegram 请填写用户名（如 @example）或 t.me 链接。"


def get_submission_contact(db: Session) -> dict:
    data = {k[len("contact_"):]: get_setting(db, k, "") for k in CONTACT_KEYS}
    tg = data.get("telegram") or ""
    if tg.startswith("@"):
        data["telegram_label"] = tg
        data["telegram_url"] = "https://t.me/" + tg[1:]
    elif tg.startswith("https://t.me/"):
        data["telegram_label"] = tg[len("https://"):]
        data["telegram_url"] = tg
    else:
        data["telegram_label"] = data["telegram_url"] = ""
    data["configured"] = bool(data["telegram_url"] or data.get("email") or data.get("note"))
    return data


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(AppSetting, key)
    return row.value if row and row.value is not None else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


HERO_EYEBROW_DEFAULT = "ONLINE NOW"
HERO_TITLE_DEFAULT = "把夜晚交给节奏"
HERO_EYEBROW_MAX = 80
HERO_TITLE_MAX = 120


def hero_copy_stored(db: Session) -> dict:
    return {
        "eyebrow": get_setting(db, "hero_eyebrow", ""),
        "title": get_setting(db, "hero_title", ""),
    }


def hero_copy_public(db: Session) -> dict:
    stored = hero_copy_stored(db)
    return {
        "hero_eyebrow": (stored["eyebrow"] or "").strip() or HERO_EYEBROW_DEFAULT,
        "hero_title": (stored["title"] or "").strip() or HERO_TITLE_DEFAULT,
    }


def tg_settings(db: Session) -> dict:
    enabled = get_setting(db, "tg_auto_enabled", "1") == "1"
    try:
        interval = int(get_setting(db, "tg_interval_min", str(TG_DEFAULT_INTERVAL_MIN)))
    except ValueError:
        interval = TG_DEFAULT_INTERVAL_MIN
    if interval not in TG_INTERVAL_CHOICES:
        interval = TG_DEFAULT_INTERVAL_MIN
    try:
        pub_delay = int(get_setting(db, "tg_auto_publish_delay", str(TG_AUTO_PUBLISH_DELAY_DEFAULT)))
    except ValueError:
        pub_delay = TG_AUTO_PUBLISH_DELAY_DEFAULT
    if pub_delay not in TG_AUTO_PUBLISH_DELAY_CHOICES:
        pub_delay = TG_AUTO_PUBLISH_DELAY_DEFAULT
    return {
        "enabled": enabled,
        "interval": interval,
        "last_run": get_setting(db, "tg_last_run", ""),
        "last_result": get_setting(db, "tg_last_result", ""),
        "auto_publish": get_setting(db, "tg_auto_publish", "1") == "1",
        "auto_publish_delay": pub_delay,
        "last_auto_publish": get_setting(db, "tg_last_auto_publish", ""),
        "last_eng_sync": get_setting(db, "tg_last_eng_sync", ""),
    }


# ---------------------------------------------------------------------------
# Telegram collection filters (过滤内容)
# ---------------------------------------------------------------------------

TG_HANDLE_RE = re.compile(r"@[A-Za-z0-9_]{3,}")
TG_PROMO_WORDS = (
    "投稿", "关注", "频道", "客服", "联系", "商务", "合作", "订阅", "爆料",
    "点击", "加入", "官方", "私聊", "大事件", "tme",
)
_tg_hash_cache: dict[str, Optional[tuple[int, int]]] = {}


def tg_norm(value: str) -> str:
    """Fold width/case and drop spaces, emoji, arrows and punctuation (keep @ and #)."""
    folded = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(ch for ch in folded if ch.isalnum() or ch in "@#")


def tg_url_regex(pattern: str) -> Optional[re.Pattern]:
    """Literal-ish matcher for t.me / telegram.me / http patterns, keeping other hosts intact."""
    raw = (pattern or "").strip()
    m = re.search(
        r"(?i)(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(\S+)",
        raw,
    )
    if not m and not re.match(r"(?i)^https?://", raw):
        return None
    if not m:
        return re.compile(re.escape(raw), re.IGNORECASE)
    path = m.group(1).strip().strip("/")
    if not path:
        return None
    parts = [p for p in path.split("/") if p]
    body = "/".join(re.escape(x) for x in parts)
    return re.compile(
        r"(?i)(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/" + body + r"(?:/\d+)?(?!\w)"
    )


def tg_rule_regex(pattern: str) -> Optional[re.Pattern]:
    url_rx = tg_url_regex(pattern)
    if url_rx:
        return url_rx
    raw = (pattern or "").strip()
    prefix = raw.endswith(("：", ":"))
    norm = tg_norm(pattern)
    if not norm:
        return None
    def cls(ch: str) -> str:
        # Match the raw text directly: accept the full-width form of ASCII letters, digits, @ and #.
        if ch.isascii() and (ch.isalnum() or ch in "@#"):
            return "[" + re.escape(ch) + re.escape(chr(ord(ch) + 0xFEE0)) + "]"
        return re.escape(ch)

    body = r"[\W_]*?".join(cls(ch) for ch in norm)
    if norm.endswith("@"):
        body += r"[A-Za-z0-9_]+"
    if prefix:
        # "此前文章：" etc. only at the start of a line, requiring the colon so mid-sentence
        # 「此前文章指出」 is not stripped.
        return re.compile(r"(?m)^[\s\W_]*" + body + r"[：:]", re.IGNORECASE)
    return re.compile(body, re.IGNORECASE)


def tg_promo_residue(value: str) -> bool:
    """True when what is left of a line is only handles/links/promo words."""
    rest = URL_RE.sub(" ", value or "")
    rest = re.sub(r"(?:t\.me|telegram\.me)/\S+", " ", rest, flags=re.IGNORECASE)
    rest = TG_HANDLE_RE.sub(" ", rest)
    norm = tg_norm(rest).replace("@", "").replace("#", "")
    for word in TG_PROMO_WORDS:
        norm = norm.replace(word, "")
    return len(norm) < 6


def tg_hash_parse(raw: Optional[str]) -> Optional[tuple[int, int]]:
    try:
        d, a = (raw or "").split(":")
        return int(d, 16), int(a, 16)
    except ValueError:
        return None


def tg_hash_str(value: tuple[int, int]) -> str:
    return f"{value[0]:016x}:{value[1]:016x}"


def tg_image_hash_file(path: Path) -> Optional[tuple[int, int]]:
    """64-bit difference hash + 64-bit average hash of an image (perceptual)."""
    if PILImage is None:
        return None
    try:
        with PILImage.open(path) as im:
            try:
                im.seek(0)
            except Exception:  # noqa: BLE001
                pass
            gray = im.convert("L")
            dpx = list(gray.resize((9, 8), PILImage.LANCZOS).getdata())
            apx = list(gray.resize((8, 8), PILImage.LANCZOS).getdata())
    except Exception:  # noqa: BLE001
        return None
    dh = 0
    for row in range(8):
        for col in range(8):
            dh = (dh << 1) | (1 if dpx[row * 9 + col] > dpx[row * 9 + col + 1] else 0)
    avg = sum(apx) / 64.0
    ah = 0
    for v in apx:
        ah = (ah << 1) | (1 if v > avg else 0)
    return dh, ah


def tg_media_hash(name: Optional[str]) -> Optional[tuple[int, int]]:
    if not name or not ANN_MEDIA_NAME_RE.match(name) or ann_media_type(name) != "image":
        return None
    if name in _tg_hash_cache:
        return _tg_hash_cache[name]
    path = ANN_MEDIA_DIR / name
    if not path.is_file():
        return None
    value = tg_image_hash_file(path)
    if len(_tg_hash_cache) > 5000:
        _tg_hash_cache.clear()
    _tg_hash_cache[name] = value
    return value


def tg_hash_too_plain(value: tuple[int, int]) -> bool:
    bits = bin(value[0]).count("1")
    return bits < 6 or bits > 58


def tg_hash_distance(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    return bin(a[0] ^ b[0]).count("1"), bin(a[1] ^ b[1]).count("1")


def tg_filter_settings(db: Session) -> dict:
    level = get_setting(db, "tg_filter_level", "normal")
    if level not in TG_FILTER_THRESHOLDS:
        level = "normal"
    return {
        "autolearn": get_setting(db, "tg_filter_autolearn", "1") == "1",
        "gamble": get_setting(db, "tg_filter_gamble", "1") == "1",
        "level": level,
        "threshold": TG_FILTER_THRESHOLDS[level],
    }


def tg_gamble_weight(rule: "TgFilterRule") -> int:
    try:
        w = int(rule.weight or 0)
    except (TypeError, ValueError):
        w = 0
    if w > 0:
        return w
    return 2


def tg_rules_ctx(db: Session) -> dict:
    rules = db.scalars(select(TgFilterRule).order_by(TgFilterRule.id)).all()
    cfg = tg_filter_settings(db)
    ctx_data = {
        "strip": [], "block": [], "gamble": [], "images": [],
        "autolearn": cfg["autolearn"], "gamble_on": cfg["gamble"], "threshold": cfg["threshold"],
    }
    for rule in rules:
        if rule.mode == "image":
            h = tg_hash_parse(rule.img_hash)
            if h:
                ctx_data["images"].append((rule, h))
        elif rule.mode in ("strip", "block"):
            rx = tg_rule_regex(rule.pattern)
            if rx:
                ctx_data[rule.mode].append((rule, rx, tg_norm(rule.pattern)))
        elif rule.mode == "gamble":
            rx = tg_rule_regex(rule.pattern)
            if rx:
                ctx_data["gamble"].append((rule, rx, tg_norm(rule.pattern), tg_gamble_weight(rule)))
    return ctx_data


def tg_block_hit(ctx_data: dict, raw: str) -> Optional[TgFilterRule]:
    hay = tg_norm(raw)
    if not hay:
        return None
    for rule, _rx, norm in ctx_data["block"]:
        if norm and norm in hay:
            return rule
    return None


def tg_gamble_hit(ctx_data: dict, raw: str) -> Optional[tuple[list, int]]:
    """Return (rules, score) when the post looks like a gambling ad, else None.

    Weak keywords (点数, 充值, 体育…) never trigger alone. News / 曝光 / 避雷 posts
    are skipped unless they also contain a hard advertising CTA (返水, 首充, 点击上桌…).
    """
    if not ctx_data.get("gamble_on"):
        return None
    text = raw or ""
    hay = tg_norm(text)
    if not hay:
        return None
    hits: list = []
    seen: set = set()
    score = 0
    max_w = 0
    has_cta = False
    for rule, rx, norm, weight in ctx_data.get("gamble") or []:
        ok = bool(norm and norm in hay)
        if not ok and rx is not None:
            ok = bool(rx.search(text))
        if not ok:
            continue
        key = norm or rule.pattern
        if key in seen:
            continue
        seen.add(key)
        hits.append(rule)
        score += weight
        if weight > max_w:
            max_w = weight
        if tg_norm(rule.pattern) in TG_GAMBLE_CTA_NORMS or key in TG_GAMBLE_CTA_NORMS:
            has_cta = True
    if not hits:
        return None
    if score < TG_GAMBLE_SCORE_MIN and not (max_w >= 4):
        return None
    if len(hits) < 2 and max_w < 4:
        return None
    if TG_GAMBLE_NEWS_RE.search(text) and not has_cta:
        return None
    return hits, score


# Promo fragments the user asked to strip (not whole-post ignore):
# 1) #网友投稿 hashtag and its t.me ?q= search links (encoded or decoded)
# 2) t.me/tx188 (and telegram.me / @tx188) in all common URL shapes, plus HTML anchors.
_TG_WANGYOU_ENC = r"%E7%BD%91%E5%8F%8B%E6%8A%95%E7%A8%BF"
_TG_WANGYOU_Q = r"(?:%23|#)(?:网友投稿|" + _TG_WANGYOU_ENC + r")"
_TG_WANGYOU_LINK_RE = re.compile(
    r"(?i)(?:https?://)?(?:www\.)?(?:t(?:elegram)?\.me)/[^\s<>'\"?#]+"
    r"\?q=" + _TG_WANGYOU_Q + r"(?:&[^\s<>'\"]*)?"
    r"|(?:[?&]q=" + _TG_WANGYOU_Q + r"(?:&[^\s<>'\"]*)?)"
    r"|" + _TG_WANGYOU_ENC
)
_TG_WANGYOU_TAG_RE = re.compile(r"#网友投稿(?:\s*[：:]\s*)?")
_TG_TX188_RE = re.compile(
    r"(?i)(?:https?://)?(?:www\.)?(?:t(?:elegram)?\.me)/(?:s/)?tx188(?:/\d+)?/?(?:[?#][^\s<>'\"]*)?"
    r"|(?<![A-Za-z0-9_])@tx188(?![A-Za-z0-9_])"
)
_TG_ANCHOR_RE = re.compile(r"(?is)<a\b([^>]*)>(.*?)</a>")
_TG_HREF_RE = re.compile(r"""(?is)\bhref\s*=\s*['"]([^'"]+)['"]""")


def _tg_is_wangyou_or_tx188(value: str) -> bool:
    s = value or ""
    if not s:
        return False
    if _TG_WANGYOU_LINK_RE.search(s) or _TG_WANGYOU_TAG_RE.search(s) or _TG_TX188_RE.search(s):
        return True
    return bool(re.search(r"(?i)(?:t(?:elegram)?\.me)/tx188\b|#网友投稿|" + _TG_WANGYOU_ENC, s))


def _tg_drop_matching_anchors(text: str) -> str:
    def repl(m: re.Match) -> str:
        attrs, inner = m.group(1) or "", m.group(2) or ""
        href_m = _TG_HREF_RE.search(attrs)
        href = href_m.group(1) if href_m else ""
        if _tg_is_wangyou_or_tx188(href) or _tg_is_wangyou_or_tx188(inner):
            return " "
        return m.group(0)
    return _TG_ANCHOR_RE.sub(repl, text or "")


def tidy_stripped_text(text: str) -> str:
    """Collapse leftover blank lines and dangling separators after a promo strip."""
    lines: list[str] = []
    for line in (text or "").split("\n"):
        work = re.sub(r"[ \t\u00a0]{2,}", " ", line).strip()
        work = re.sub(r"^[：:|·•\-—]+\s*", "", work)
        work = work.rstrip(" \t,，、;；:：|·•-—")
        lines.append(work)
    txt = "\n".join(lines)
    txt = re.sub(r"[ \t\u00a0]+\n", "\n", txt)
    txt = re.sub(r"\n[ \t\u00a0]*(?=\n)", "\n", txt)
    return re.sub(r"\n{3,}", "\n\n", txt).strip()


def strip_wangyou_tx188(text: str) -> str:
    """Remove only #网友投稿 / tx188 promo fragments; keep the rest of the post."""
    s = _tg_drop_matching_anchors(text or "")
    s = _TG_WANGYOU_LINK_RE.sub(" ", s)
    s = _TG_WANGYOU_TAG_RE.sub(" ", s)
    s = _TG_TX188_RE.sub(" ", s)
    return tidy_stripped_text(s)


def strip_wangyou_tx188_links(items: list[dict]) -> list[dict]:
    """Drop or clean structured announcement links that only point at these targets."""
    out: list[dict] = []
    for item in items or []:
        url = (item.get("url") or "").strip()
        label = (item.get("text") or "").strip()
        if _tg_is_wangyou_or_tx188(url):
            continue
        new_label = strip_wangyou_tx188(label) if label else label
        if not new_label and _tg_is_wangyou_or_tx188(label):
            continue
        rest = {k: v for k, v in item.items() if k not in ("text", "url")}
        out.append({"text": new_label or label, "url": url, **rest})
    return out


def tg_strip_text(ctx_data: dict, raw: str) -> tuple[str, list[TgFilterRule]]:
    hits: dict[int, TgFilterRule] = {}
    out: list[str] = []
    raw = strip_wangyou_tx188(raw or "")
    for line in (raw or "").split("\n"):
        work = line
        matched = False
        for rule, rx, _norm in ctx_data["strip"]:
            if rx.search(work):
                work = rx.sub(" ", work)
                hits[rule.id] = rule
                matched = True
        if not matched:
            out.append(line)
            continue
        if tg_promo_residue(work):
            continue
        out.append(re.sub(r"[ \t]{2,}", " ", work).strip().rstrip(" \t,，、;；:：|-—➡️➞→👉"))
    txt = tidy_stripped_text("\n".join(out))
    return txt, list(hits.values())


def tg_image_rule_hit(ctx_data: dict, value: Optional[tuple[int, int]]) -> Optional[TgFilterRule]:
    if not value:
        return None
    limit = ctx_data["threshold"]
    for rule, h in ctx_data["images"]:
        dd, ad = tg_hash_distance(value, h)
        if dd <= limit and ad <= limit + 4:
            return rule
    return None


def tg_save_sample(src: Path) -> Optional[str]:
    if PILImage is None:
        return None
    try:
        TG_FILTER_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}.jpg"
        with PILImage.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((240, 240))
            im.save(TG_FILTER_DIR / name, "JPEG", quality=82)
        return name
    except Exception:  # noqa: BLE001
        return None


def tg_add_image_rule(
    db: Session, ctx_data: Optional[dict], src: Path, value: tuple[int, int], label: str, source: str
) -> str:
    """Returns 'added', 'dup' or 'plain'."""
    if tg_hash_too_plain(value):
        return "plain"
    existing = ctx_data["images"] if ctx_data is not None else [
        (r, h) for r in db.scalars(select(TgFilterRule).where(TgFilterRule.mode == "image")).all()
        if (h := tg_hash_parse(r.img_hash))
    ]
    for _rule, h in existing:
        dd, ad = tg_hash_distance(value, h)
        if dd <= 3 and ad <= 6:
            return "dup"
    rule = TgFilterRule(
        mode="image", pattern="", img_hash=tg_hash_str(value), sample=tg_save_sample(src),
        label=(label or "")[:100], source=source, hits=0,
    )
    db.add(rule)
    db.flush()
    if ctx_data is not None:
        ctx_data["images"].append((rule, value))
    return "added"


def tg_learn_from_media(db: Session, ctx_data: Optional[dict], media: list[dict], label: str, source: str) -> int:
    added = 0
    if source == "auto":
        auto_count = db.scalar(
            select(func.count()).select_from(TgFilterRule).where(TgFilterRule.source == "auto")
        ) or 0
        if auto_count >= TG_FILTER_AUTO_MAX:
            return 0
    for m in media:
        name = m.get("poster_file") if m["type"] == "video" else m["file"]
        value = tg_media_hash(name)
        if not value:
            continue
        if tg_add_image_rule(db, ctx_data, ANN_MEDIA_DIR / name, value, label, source) == "added":
            added += 1
    return added



def tg_media_paths(media: list[dict]) -> list[Path]:
    """Image (or video poster) paths for hashing / OCR."""
    paths: list[Path] = []
    for m in media or []:
        name = m.get("poster_file") or (m.get("poster") if m.get("type") == "video" else None) or m.get("file")
        if m.get("type") == "video" and m.get("poster"):
            name = m.get("poster")
        if not name or not ANN_MEDIA_NAME_RE.match(str(name)):
            continue
        path = ANN_MEDIA_DIR / name
        if path.is_file():
            paths.append(path)
    return paths


def tg_post_haystack(post: "TgPost", raw: str, ocr_text: str = "") -> str:
    parts = [raw or "", post.text or ""]
    links: list[str] = []
    for item in ann_links(post_links_obj(post)):
        if item.get("url"):
            links.append(item["url"])
        if item.get("text"):
            links.append(item["text"])
    extra = tgf3.collect_extra_text(links=links)
    if extra:
        parts.append(extra)
    if ocr_text:
        parts.append(ocr_text)
    return "\n".join(parts)


def tg_maybe_ocr(raw: str, media: list[dict]) -> str:
    """OCR images of short or lottery/venue-ish posts; empty if tesseract missing."""
    if not tgf3.ocr_available():
        return ""
    if not media:
        return ""
    norm = tgf3.tg_norm(raw or "")
    interesting = bool(
        tgf3.VENUE_WORD_RE.search(raw or "")
        or tgf3.LOTTERY_HARD_RE.search(raw or "")
        or re.search(r"开奖|彩票|开出|特码|期号|赌|豹子", raw or "")
        or len(norm) < 400
    )
    if not interesting:
        return ""
    return tgf3.ocr_media_files(tg_media_paths(media))


def tg_manual_rules(ctx_data: dict) -> list:
    """User-added 过滤池 terms (any text mode) — honoured as hard blocks."""
    out = []
    for key in ("strip", "block", "gamble"):
        for item in ctx_data.get(key) or []:
            rule = item[0]
            if getattr(rule, "source", "") == "manual" and (rule.pattern or "").strip():
                out.append(item)
    return out


def tg_manual_hit(ctx_data: dict, raw: str) -> Optional["TgFilterRule"]:
    hay = tgf3.tg_norm(raw)
    if not hay:
        return None
    for item in tg_manual_rules(ctx_data):
        rule = item[0]
        rx = item[1] if len(item) > 1 else None
        norm = tgf3.tg_norm(rule.pattern)
        ok = bool(norm and norm in hay)
        if not ok and rx is not None:
            try:
                ok = bool(rx.search(raw))
            except Exception:  # noqa: BLE001
                ok = False
        if ok:
            return rule
    return None


def hide_announcement(ann: "Announcement", reason: str) -> None:
    ann.hidden = True
    ann.hidden_at = datetime.now(timezone.utc).replace(tzinfo=None)
    ann.hidden_reason = (reason or "广告")[:300]


def unhide_announcement(ann: "Announcement") -> None:
    ann.hidden = False
    ann.hidden_at = None
    ann.hidden_reason = None


def tg_filter_post(db: Session, post: TgPost, ctx_data: dict) -> tuple[Optional[str], list[str]]:
    """Apply filters to one unpublished post. Returns (action, media names to delete)."""
    if post.status != TG_STATUS_NEW:
        return None, []
    raw = post.raw_text if post.raw_text is not None else (post.text or "")
    if post.raw_text is None:
        post.raw_text = raw
    media = parse_media_json(post.media_json)
    ocr_text = ""
    hay = tg_post_haystack(post, raw, "")
    if not post.filter_override:
        # OCR when text alone is inconclusive; captions/buttons already in hay.
        if not tgf3.adv_block_reason(hay) and not tg_block_hit(ctx_data, hay) and not tg_manual_hit(ctx_data, hay):
            ocr_text = tg_maybe_ocr(hay, media)
            if ocr_text:
                hay = tg_post_haystack(post, raw, ocr_text)

        def _ignore(rule_label: str, learn_label: str) -> tuple[str, list[str]]:
            if ctx_data["autolearn"] and media:
                tg_learn_from_media(db, ctx_data, media, f"{learn_label} #{post.post_id}", "auto")
            post.status = TG_STATUS_IGNORED
            post.media_json = None
            post.text = raw
            post.filter_info = rule_label[:300]
            return "ignored", media_names(media)

        # User-added 过滤池 terms are hard blocks (normalized). News exemption still applies
        # unless an ad CTA / contact pitch is present.
        man = tg_manual_hit(ctx_data, hay)
        if man and not tgf3.news_exempt(hay):
            man.hits = (man.hits or 0) + 1
            return _ignore(f"整条忽略：过滤池「{man.pattern}」", f"自动：{man.pattern}")

        adv = tgf3.adv_block_reason(hay)
        if adv:
            return _ignore(f"整条忽略：{adv}", "自动：广告规则")

        rule = tg_block_hit(ctx_data, hay) or tg_block_hit(ctx_data, raw)
        if rule:
            # Bare keyword blocks (e.g. 六合彩) respect news exemption; ad tags never do.
            pat = rule.pattern or ""
            if pat.startswith("#") or not tgf3.news_exempt(hay):
                rule.hits = (rule.hits or 0) + 1
                return _ignore(f"整条忽略：命中「{rule.pattern}」", f"自动：{rule.pattern}")
        ghit = tg_gamble_hit(ctx_data, hay) or tg_gamble_hit(ctx_data, raw)
        if ghit:
            rules, score = ghit
            # tg_gamble_hit already applies news exemption (unless CTA).
            for r in rules:
                r.hits = (r.hits or 0) + 1
            names = "、".join(f"「{r.pattern}」" for r in rules[:8])
            return _ignore(f"整条忽略：赌博广告（命中{names}，分{score}）", "自动：赌博广告")
    # User's saved pending text is final for strip/mutation, but hard blocks above still apply at rescan and publish.
    if post.content_edited:
        return None, []
    new_text, strip_hits = tg_strip_text(ctx_data, raw)
    kept: list[dict] = []
    removed: list[dict] = []
    for m in media:
        name = m.get("poster_file") if m["type"] == "video" else m["file"]
        rule = tg_image_rule_hit(ctx_data, tg_media_hash(name))
        if rule:
            rule.hits = (rule.hits or 0) + 1
            removed.append(m)
        else:
            kept.append(m)
    # Ad-image match on a non-news post: ignore the whole item (do not leave a
    # caption that auto-publish would then put on the homepage).
    if removed and not post.filter_override and not tgf3.news_exempt(raw or new_text or ""):
        labels = "、".join(
            f"「{(r.label or r.pattern or '广告图')}」"
            for r in [
                tg_image_rule_hit(ctx_data, tg_media_hash(m.get("poster_file") if m.get("type")=="video" else m.get("file")))
                for m in removed
            ] if r
        ) or "广告图"
        if ctx_data["autolearn"] and media:
            tg_learn_from_media(db, ctx_data, media, f"自动：广告图片 #{post.post_id}", "auto")
        post.status = TG_STATUS_IGNORED
        post.media_json = None
        post.text = raw
        post.filter_info = f"整条忽略：命中广告图片{labels}"[:300]
        return "ignored", media_names(media)
    if new_text != (post.text or ""):
        for rule in strip_hits:
            rule.hits = (rule.hits or 0) + 1
    changed = new_text != (post.text or "") or bool(removed)
    post.text = new_text
    if removed:
        post.media_json = dump_media(kept)
    info: list[str] = []
    old_info = post.filter_info or ""
    prev_removed = re.search(r"已移除 (\d+) 个推广图片", old_info)
    removed_total = len(removed) + (int(prev_removed.group(1)) if prev_removed else 0)
    if strip_hits:
        info.append("已去除推广语")
    if removed_total:
        info.append(f"已移除 {removed_total} 个推广图片/视频")
    if not tg_norm(new_text) and not kept and (raw.strip() or removed_total):
        post.status = TG_STATUS_IGNORED
        post.media_json = None
        post.filter_info = ("整条忽略：仅含推广内容" + ("（" + "，".join(info) + "）" if info else ""))[:300]
        return "ignored", media_names(media)
    if not tg_norm(new_text) and raw.strip() and kept:
        info.append("文字仅为推广语，已去除，请确认媒体内容")
    if post.filter_override:
        info.append("已手动恢复")
    post.filter_info = "；".join(info)[:300] if info else None
    return ("changed" if changed else None), media_names(removed)


def apply_tg_filters(db: Session) -> tuple[int, int]:
    """Re-scan every pending (unpublished) collected post with the current rules."""
    ctx_data = tg_rules_ctx(db)
    ignored = changed = 0
    names: list[str] = []
    for post in db.scalars(select(TgPost).where(TgPost.status == TG_STATUS_NEW)).all():
        try:
            action, dropped = tg_filter_post(db, post, ctx_data)
        except Exception:  # noqa: BLE001
            logging.exception("tg filter failed on post %s (tg#%s)", post.id, post.post_id)
            continue
        names += dropped
        if action == "ignored":
            ignored += 1
        elif action == "changed":
            changed += 1
    db.commit()
    delete_unreferenced_media(db, names)
    return ignored, changed


def seed_tg_filter_rules(db: Session) -> None:
    if get_setting(db, "tg_filter_seeded", "") == "1":
        return
    taken = {(r.mode, tg_norm(r.pattern)) for r in db.scalars(select(TgFilterRule)).all()}
    for mode, pattern in DEFAULT_TG_FILTER_RULES:
        key = (mode, tg_norm(pattern))
        if key[1] and key not in taken:
            db.add(TgFilterRule(mode=mode, pattern=pattern, label="默认", source="seed"))
            taken.add(key)
    db.commit()
    set_setting(db, "tg_filter_seeded", "1")


def seed_tg_filter_rules_v2(db: Session) -> None:
    """Add contact/self-link strip rules and the 赌博广告 group without duplicating older seeds."""
    if get_setting(db, "tg_filter_seeded_v2", "") != "1":
        taken = {(r.mode, tg_norm(r.pattern)) for r in db.scalars(select(TgFilterRule)).all()}
        for mode, pattern in DEFAULT_TG_STRIP_RULES_V2:
            key = (mode, tg_norm(pattern))
            if key[1] and key not in taken:
                db.add(TgFilterRule(mode=mode, pattern=pattern, label="频道联系/自链", source="seed"))
                taken.add(key)
        db.commit()
        set_setting(db, "tg_filter_seeded_v2", "1")
    if get_setting(db, "tg_filter_gamble_seeded", "") == "1":
        if get_setting(db, "tg_filter_gamble", "") == "":
            set_setting(db, "tg_filter_gamble", "1")
        return
    taken = {(r.mode, tg_norm(r.pattern)) for r in db.scalars(select(TgFilterRule)).all()}
    for pattern, weight in DEFAULT_TG_GAMBLE_RULES:
        key = ("gamble", tg_norm(pattern))
        if key[1] and key not in taken:
            db.add(TgFilterRule(
                mode="gamble", pattern=pattern, label="赌博广告", source="seed", weight=weight,
            ))
            taken.add(key)
    db.commit()
    set_setting(db, "tg_filter_gamble_seeded", "1")
    if get_setting(db, "tg_filter_gamble", "") == "":
        set_setting(db, "tg_filter_gamble", "1")



def seed_tg_filter_rules_v3(db: Session) -> None:
    """Stronger lottery/venue/sponsor/domain rules, plus pHash seeds of known ad images."""
    if get_setting(db, "tg_filter_seeded_v3", "") != "1":
        taken = {(r.mode, tg_norm(r.pattern)) for r in db.scalars(select(TgFilterRule)).all()}
        for pattern in tgf3.SEED_V3_BLOCK:
            key = ("block", tg_norm(pattern))
            if key[1] and key not in taken:
                db.add(TgFilterRule(
                    mode="block", pattern=pattern, label="广告强化", source="seed",
                ))
                taken.add(key)
        for pattern, weight in tgf3.SEED_V3_GAMBLE:
            key = ("gamble", tg_norm(pattern))
            if key[1] and key not in taken:
                db.add(TgFilterRule(
                    mode="gamble", pattern=pattern, label="彩票/开奖强化", source="seed", weight=weight,
                ))
                taken.add(key)
            elif key in taken:
                row = db.scalar(
                    select(TgFilterRule).where(
                        TgFilterRule.mode == "gamble", TgFilterRule.pattern == pattern
                    )
                )
                if row and (row.weight or 0) < weight:
                    row.weight = weight
        # Strengthen existing weak 开奖/彩票 scores if still at the old default.
        for pattern, weight in (("开奖", 3), ("彩票", 3)):
            for row in db.scalars(select(TgFilterRule).where(TgFilterRule.mode == "gamble")).all():
                if tg_norm(row.pattern) == tg_norm(pattern) and (row.weight or 0) < weight:
                    row.weight = weight
        db.commit()
        set_setting(db, "tg_filter_seeded_v3", "1")
    seed_tg_offender_phash(db)



DEFAULT_TG_STRIP_RULES_V4 = (
    ("strip", "?q=%23%E7%BD%91%E5%8F%8B%E6%8A%95%E7%A8%BF", "网友投稿话题链接"),
    ("strip", "#网友投稿", "网友投稿话题标签"),
    ("strip", "https://t.me/tx188", "投稿联系 tx188"),
)


def backfill_wangyou_tx188(db: Session) -> dict:
    """Strip #网友投稿 / tx188 fragments from published announcements and pending posts.

    Does not delete, hide, or change media / engagement fields.
    """
    ann_ids: list[int] = []
    pending_ids: list[int] = []
    for ann in db.scalars(select(Announcement).order_by(Announcement.id)).all():
        changed = False
        original = ann.content or ""
        if _tg_is_wangyou_or_tx188(original):
            new_content = strip_wangyou_tx188(original)
            if new_content != original:
                ann.content = new_content
                changed = True
        if ann.links_json:
            old_links = ann_links(ann)
            new_links = strip_wangyou_tx188_links(old_links)
            if new_links != old_links:
                ann.links_json = dump_links(new_links)
                changed = True
        if changed:
            ann_ids.append(ann.id)
    for post in db.scalars(select(TgPost).where(TgPost.status == TG_STATUS_NEW).order_by(TgPost.id)).all():
        pchanged = False
        original = post.text or ""
        if _TG_WANGYOU_LINK_RE.search(original) or _TG_WANGYOU_TAG_RE.search(original) or _TG_TX188_RE.search(original) or _tg_is_wangyou_or_tx188(original):
            new_text = strip_wangyou_tx188(original)
            if new_text != original:
                post.text = new_text
                pchanged = True
        if getattr(post, "edit_links_json", None):
            old_links = ann_links(post_links_obj(post))
            new_links = strip_wangyou_tx188_links(old_links)
            if new_links != old_links:
                post.edit_links_json = dump_links(new_links)
                pchanged = True
        if pchanged:
            pending_ids.append(post.id)
    if ann_ids or pending_ids:
        db.commit()
    logging.info("wangyou/tx188 backfill announcements=%s pending=%s", len(ann_ids), len(pending_ids))
    return {"announcements": ann_ids, "pending": pending_ids}


def seed_tg_filter_rules_v4(db: Session) -> None:
    """Visible strip rules for #网友投稿 search links and t.me/tx188; backfill published posts once."""
    if get_setting(db, "tg_filter_seeded_v4", "") == "1":
        return
    # User may have added the same strings as manual *block* rules; convert those to strip
    # so they do not ignore the whole post, and they show under 「去除推广语」.
    targets = {tg_norm(p) for _, p, _ in DEFAULT_TG_STRIP_RULES_V4}
    for rule in db.scalars(select(TgFilterRule)).all():
        if not (rule.pattern or "").strip():
            continue
        if tg_norm(rule.pattern) in targets and rule.mode == "block":
            rule.mode = "strip"
            if rule.source == "manual":
                rule.source = "seed"
            if not (rule.label or "").strip():
                for _m, pat, lab in DEFAULT_TG_STRIP_RULES_V4:
                    if tg_norm(pat) == tg_norm(rule.pattern):
                        rule.label = lab
                        break
    db.commit()
    taken = {(r.mode, tg_norm(r.pattern)) for r in db.scalars(select(TgFilterRule)).all()}
    for mode, pattern, label in DEFAULT_TG_STRIP_RULES_V4:
        key = (mode, tg_norm(pattern))
        if key[1] and key not in taken:
            db.add(TgFilterRule(mode=mode, pattern=pattern, label=label, source="seed"))
            taken.add(key)
        else:
            # If already present as strip, ensure a visible label.
            for row in db.scalars(select(TgFilterRule).where(TgFilterRule.mode == "strip")).all():
                if tg_norm(row.pattern) == key[1] and not (row.label or "").strip():
                    row.label = label
                    if row.source == "manual":
                        row.source = "seed"
    db.commit()
    try:
        result = backfill_wangyou_tx188(db)
        try:
            (DATA_DIR / "wangyou_tx188_backfill.json").write_text(
                json.dumps(result, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            logging.exception("could not write backfill report")
    except Exception:  # noqa: BLE001
        logging.exception("wangyou/tx188 backfill failed")
        db.rollback()
    set_setting(db, "tg_filter_seeded_v4", "1")
    db.commit()


def seed_tg_offender_phash(db: Session) -> None:
    """Add perceptual hashes of known offending post images (not user screenshots)."""
    if get_setting(db, "tg_filter_phash_offenders", "") == "1":
        return
    samples = (
        ("4f59ebcbce8b4b65bf4ceb2cd54be2b9.jpg", "彩票开奖图 #81875"),
        ("526a762234594426a394846721a1e23c.jpg", "会所广告图 #81900"),
        ("2c18197eefaa45b6ac37de6e59a0cc65.jpg", "会所广告图 #81900-2"),
    )
    ctx_data = tg_rules_ctx(db)
    added = 0
    for name, label in samples:
        path = ANN_MEDIA_DIR / name
        if not path.is_file():
            continue
        value = tg_image_hash_file(path)
        if not value:
            continue
        if tg_add_image_rule(db, ctx_data, path, value, label, "seed") == "added":
            added += 1
    db.commit()
    set_setting(db, "tg_filter_phash_offenders", "1")
    logging.info("seeded %s offender pHash rules", added)


def tg_collect_cutoff() -> datetime:
    """Start of the rolling 1-month window in 东南亚DJ时间 (UTC+8)."""
    now = datetime.now(TZ_CN)
    month = now.month - 1 or 12
    year = now.year if now.month > 1 else now.year - 1
    day = min(now.day, calendar.monthrange(year, month)[1])
    return now.replace(year=year, month=month, day=day)


def tg_as_cn(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TZ_CN)


def tg_in_collect_window(posted_at: Optional[datetime], cutoff: datetime) -> bool:
    aware = tg_as_cn(posted_at)
    if aware is None:
        return True
    return aware >= cutoff


def tg_preview_before(html_text: str) -> Optional[str]:
    m = re.search(r'data-before="(\d+)"', html_text)
    return m.group(1) if m else None


def tg_disk_free() -> int:
    try:
        ANN_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(ANN_MEDIA_DIR).free
    except OSError:
        return 0


def _tg_set_progress(db: Session, text: str) -> None:
    try:
        set_setting(db, "tg_last_result", text[:300])
    except Exception:
        pass



def auto_publish_eligible(db: Session, cap: int = TG_AUTO_PUBLISH_CAP) -> int:
    """Publish pending non-ignored posts whose posted_at (or collected_at) is older than the delay."""
    if get_setting(db, "tg_auto_publish", "1") != "1":
        return 0
    try:
        delay = int(get_setting(db, "tg_auto_publish_delay", str(TG_AUTO_PUBLISH_DELAY_DEFAULT)))
    except ValueError:
        delay = TG_AUTO_PUBLISH_DELAY_DEFAULT
    if delay not in TG_AUTO_PUBLISH_DELAY_CHOICES:
        delay = TG_AUTO_PUBLISH_DELAY_DEFAULT
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    threshold = now - timedelta(minutes=delay)
    cutoff_naive = tg_collect_cutoff().astimezone(timezone.utc).replace(tzinfo=None)
    posts = db.scalars(
        select(TgPost).where(TgPost.status == TG_STATUS_NEW).order_by(TgPost.post_id.asc())
    ).all()
    published = 0
    for post in posts:
        if published >= cap:
            break
        ts = post.posted_at or post.collected_at
        if ts is None:
            continue
        if ts < cutoff_naive:
            continue
        if ts > threshold:
            continue
        ok, _reason = publish_one_tg_post(db, post, False)
        if ok:
            published += 1
    if published:
        db.commit()
        now_txt = datetime.now(timezone.utc).astimezone(TZ_CN).strftime("%Y-%m-%d %H:%M")
        set_setting(db, "tg_last_auto_publish", f"{now_txt}（{published} 条）")
    else:
        db.commit()
    return published


def copy_engagement_to_ann(ann: Announcement, post: TgPost) -> None:
    ann.tg_reactions_json = post.tg_reactions_json
    ann.tg_views = post.tg_views
    ann.tg_comment_count = post.tg_comment_count
    ann.tg_synced_at = post.tg_synced_at


def collect_telegram(trigger: str = "manual") -> str:
    """Walk the public preview, store new posts as drafts, sync engagement, maybe auto-publish.

    Filters from the 过滤内容 tab are applied as each post is stored.
    Engagement (reactions / views / comment count if any) is written for new and existing posts.
    Eligible pending items may be auto-published when that setting is on.
    """
    if not _tg_lock.acquire(blocking=False):
        _tg_state["running"] = False
        return "已有采集任务正在进行，请稍后再试。"
    db = SessionLocal()
    try:
        _tg_state["running"] = True
        cutoff = tg_collect_cutoff()
        cutoff_txt = cutoff.strftime("%Y-%m-%d %H:%M")
        existing = set(
            db.scalars(select(TgPost.post_id).where(TgPost.channel == TG_CHANNEL)).all()
        )
        added = 0
        filtered = 0
        gamble_n = 0
        skipped_old = 0
        skipped_existing = 0
        media_count = 0
        failed_media = 0
        pages = 0
        fctx = tg_rules_ctx(db)
        url = TG_PREVIEW_URL
        seen_urls: set[str] = set()
        hit_cutoff = False
        result = ""
        eng_updated = 0
        today_cn = datetime.now(timezone.utc).astimezone(TZ_CN).strftime("%Y-%m-%d")
        month_needed = get_setting(db, "tg_eng_month_date", "") != today_cn
        recent_cutoff = datetime.now(timezone.utc).astimezone(TZ_CN) - timedelta(days=TG_ENG_RECENT_DAYS)
        walk_cutoff = cutoff if month_needed else recent_cutoff
        while pages < TG_COLLECT_MAX_PAGES and not hit_cutoff:
            if url in seen_urls:
                break
            seen_urls.add(url)
            try:
                with _http_get(url) as resp:
                    body = resp.read(5 * 1024 * 1024).decode("utf-8", "replace")
            except Exception as exc:  # noqa: BLE001
                if pages == 0:
                    result = f"采集失败：无法访问 {TG_PREVIEW_URL}（{type(exc).__name__}）"
                    return result
                break
            pages += 1
            posts = parse_tg_preview(body)
            if not posts and pages == 1:
                result = "采集完成：预览页没有可解析的帖子。"
                return result
            page_times = [tg_as_cn(p["posted_at"]) for p in posts if p.get("posted_at")]
            for post in posts:
                if post["post_id"] in existing:
                    skipped_existing += 1
                    if apply_tg_engagement(db, post):
                        eng_updated += 1
                    continue
                if not tg_in_collect_window(post.get("posted_at"), cutoff):
                    skipped_old += 1
                    continue
                if not post["text"] and not post["media"]:
                    continue
                stored = []
                pre_block = tg_block_hit(fctx, post["text"]) or tg_gamble_hit(fctx, post["text"]) or tgf3.adv_block_reason(post["text"])
                disk_ok = tg_disk_free() >= TG_DISK_MIN_FREE
                for media in post["media"]:
                    if pre_block:
                        # Advert: skip big downloads; only fetch stills so their hashes can be learned.
                        if not fctx["autolearn"]:
                            break
                        still = media["src"] if media["type"] == "image" else media.get("poster")
                        name = download_tg_media(still, "image") if still else None
                        if name:
                            stored.append({"file": name, "type": "image"})
                        continue
                    if media["type"] == "video":
                        poster = None
                        if media.get("poster"):
                            poster = download_tg_media(media["poster"], "image")
                        name = None
                        if disk_ok:
                            name = download_tg_media(
                                media["src"], "video", max_bytes=TG_VIDEO_COLLECT_MAX_BYTES
                            )
                        if name:
                            entry = {"file": name, "type": "video"}
                            if poster:
                                entry["poster"] = poster
                            stored.append(entry)
                            media_count += 1
                        elif poster:
                            stored.append({"file": poster, "type": "image"})
                            media_count += 1
                            post["notes"].append("视频过大或下载失败，已保留封面")
                            failed_media += 1
                        else:
                            failed_media += 1
                        continue
                    name = download_tg_media(media["src"], media["type"])
                    if not name:
                        failed_media += 1
                        continue
                    stored.append({"file": name, "type": media["type"]})
                    media_count += 1
                notes = list(post["notes"])
                if len(stored) < len(post["media"]) and not pre_block:
                    if "部分媒体下载失败" not in notes and "视频过大或下载失败，已保留封面" not in notes:
                        notes.append("部分媒体下载失败")
                posted = post["posted_at"]
                posted_naive = (
                    posted.astimezone(timezone.utc).replace(tzinfo=None)
                    if posted
                    else None
                )
                row = TgPost(
                    channel=TG_CHANNEL,
                    post_id=post["post_id"],
                    url=post["url"],
                    text=post["text"],
                    raw_text=post["text"],
                    filter_override=False,
                    content_edited=False,
                    media_json=json.dumps(stored) if stored else None,
                    note="；".join(notes)[:300] if notes else None,
                    status=TG_STATUS_NEW,
                    posted_at=posted_naive,
                )
                apply_engagement_fields(row, post)
                db.add(row)
                db.flush()
                action, dropped = tg_filter_post(db, row, fctx)
                db.commit()
                delete_unreferenced_media(db, dropped)
                existing.add(post["post_id"])
                added += 1
                if action == "ignored":
                    filtered += 1
                    if (row.filter_info or "").startswith("整条忽略：赌博广告"):
                        gamble_n += 1
            db.commit()
            if page_times and min(page_times) < walk_cutoff:
                hit_cutoff = True
            _tg_set_progress(
                db,
                f"采集中…已翻 {pages} 页，新增 {added} 条（其中 {filtered} 条已按规则忽略）",
            )
            before = tg_preview_before(body)
            if hit_cutoff or not before:
                break
            url = f"{TG_PREVIEW_URL}?before={before}"
            time.sleep(TG_COLLECT_PAGE_PAUSE)
        prune_tg_posts(db)
        if month_needed and hit_cutoff:
            set_setting(db, "tg_eng_month_date", today_cn)
        now_txt = datetime.now(timezone.utc).astimezone(TZ_CN).strftime("%Y-%m-%d %H:%M")
        if pages:
            set_setting(db, "tg_last_eng_sync", f"{now_txt}（更新 {eng_updated} 条）")
        auto_n = auto_publish_eligible(db)
        scope = "近一个月" if month_needed else f"近 {TG_ENG_RECENT_DAYS} 天"
        result = f"采集完成：{scope}（一个月窗口自 {cutoff_txt} 起，东南亚时间）新增 {added} 条"
        if filtered:
            result += f"，其中 {filtered} 条命中过滤规则已自动忽略"
            if gamble_n:
                result += f"（赌博广告 {gamble_n} 条）"
        result += f"（翻 {pages} 页，媒体 {media_count} 个"
        if failed_media:
            result += f"，{failed_media} 个下载失败或视频过大已跳过"
        if skipped_existing:
            result += f"，已有 {skipped_existing} 条已跳过"
        if eng_updated:
            result += f"，互动同步 {eng_updated} 条"
        if auto_n:
            result += f"，自动发布 {auto_n} 条"
        result += "）。也可使用「一键发布」。"
        return result
    finally:
        try:
            now_txt = datetime.now(timezone.utc).astimezone(TZ_CN).strftime("%Y-%m-%d %H:%M")
            res = locals().get("result") or ""
            label = "手动" if trigger == "manual" else "定时"
            set_setting(db, "tg_last_run", f"{now_txt}（{label}）")
            set_setting(db, "tg_last_result", res[:300])
        except Exception:
            pass
        _tg_state["running"] = False
        db.close()
        _tg_lock.release()
        try:
            _tgc_event.set()
        except Exception:  # noqa: BLE001
            pass


def prune_tg_posts(db: Session) -> None:
    """Drop unpublished posts older than the 1-month window, with a hard cap."""
    cutoff_naive = tg_collect_cutoff().astimezone(timezone.utc).replace(tzinfo=None)
    rows = db.scalars(
        select(TgPost).where(TgPost.status != TG_STATUS_PUBLISHED).order_by(TgPost.post_id.desc())
    ).all()
    stale: list[TgPost] = []
    kept = 0
    for row in rows:
        too_old = row.posted_at is not None and row.posted_at < cutoff_naive
        over_cap = kept >= TG_KEEP_ITEMS
        if too_old or over_cap:
            stale.append(row)
        else:
            kept += 1
    if not stale:
        return
    names: list[str] = []
    for row in stale:
        names += media_names(parse_media_json(row.media_json))
        db.delete(row)
    db.commit()
    delete_unreferenced_media(db, names)


async def telegram_scheduler() -> None:
    await asyncio.sleep(60)
    while True:
        wait_min = TG_DEFAULT_INTERVAL_MIN
        try:
            db = SessionLocal()
            try:
                cfg = tg_settings(db)
            finally:
                db.close()
            wait_min = cfg["interval"]
            if cfg["enabled"]:
                await asyncio.to_thread(collect_telegram, "auto")
            await asyncio.to_thread(cleanup_orphan_media)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(max(10, wait_min) * 60)



def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        migrate_schema(db)
        seed_default_block_keywords(db)
        seed_tg_filter_rules(db)
        seed_tg_filter_rules_v2(db)
        seed_tg_filter_rules_v3(db)
        seed_tg_filter_rules_v4(db)
        try:
            apply_tg_filters(db)
        except Exception:  # noqa: BLE001
            logging.exception("apply_tg_filters during init_db")
            db.rollback()
        user_count = db.scalar(select(func.count()).select_from(User)) or 0
        if user_count == 0:
            admin = User(
                email=DEFAULT_ADMIN_EMAIL,
                username="管理员",
                password_hash=hash_password("admin123456"),
                is_admin=True,
            )
            db.add(admin)
        ann_count = db.scalar(select(func.count()).select_from(Announcement)) or 0
        if ann_count == 0:
            db.add(
                Announcement(
                    title="站点已上线",
                    content="站点已上线，欢迎来到 DJ666。注册后即可播放音乐并参与公屏聊天。管理员请尽快修改默认密码。",
                )
            )
        if r2_storage.r2_enabled():
            current = (get_setting(db, "flac_quota_gb", "10") or "10").strip()
            if current in ("10", "10.0"):
                set_setting(db, "flac_quota_gb", "1000")
        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        _bdb = SessionLocal()
        try:
            await asyncio.to_thread(backfill_track_hashes, _bdb)
        finally:
            _bdb.close()
        DUP_STAGING_DIR.mkdir(parents=True, exist_ok=True)
        cleanup_dup_staging()
    except Exception:  # noqa: BLE001
        pass
    ANN_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    CHAT_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    TG_FILTER_DIR.mkdir(parents=True, exist_ok=True)
    try:
        opt_start_worker()
    except Exception:  # noqa: BLE001
        pass
    try:
        r2_storage.load_env_file(ENV_FILE)
        start_r2_worker()
    except Exception:  # noqa: BLE001
        logging.exception("r2 worker failed to start")
    try:
        tgc_start()
    except Exception:  # noqa: BLE001
        pass
    try:
        tga_start()
    except Exception:  # noqa: BLE001
        pass
    task = asyncio.create_task(telegram_scheduler())
    asyncio.get_running_loop().run_in_executor(None, _bot_webhook_on_startup)
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="DJ666", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="dj666_session",
    same_site="lax",
    https_only=False,
    max_age=60 * 60 * 24 * 14,
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class NoStoreHTMLMiddleware:
    """HTML pages depend on the login session (nav differs for guests / users);
    never let browsers / back-forward cache reuse them after login/logout."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                ctype = b""
                for k, v in headers:
                    if k.lower() == b"content-type":
                        ctype = v
                if ctype.startswith(b"text/html") and not any(k.lower() == b"cache-control" for k, _ in headers):
                    headers.append((b"cache-control", b"no-store, private"))
                    if not any(k.lower() == b"vary" for k, _ in headers):
                        headers.append((b"vary", b"Cookie"))
                    message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(NoStoreHTMLMiddleware)


class StaticRevalidateMiddleware:
    """Force browsers to revalidate /static/* so a stale style.css cannot stick."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not (scope.get("path") or "").startswith("/static/"):
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in (message.get("headers") or []) if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"public, max-age=0, must-revalidate"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(StaticRevalidateMiddleware)


class CanonicalHostMiddleware:
    """www and apex would otherwise get different host-only session cookies."""

    def __init__(self, app, canonical: str = "www.dj666.pw"):
        self.app = app
        self.canonical = canonical

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            host = ""
            for key, val in scope.get("headers") or []:
                if key == b"host":
                    host = val.decode("latin1").split(":")[0].lower()
                    break
            if host == "dj666.pw":
                path = scope.get("path") or "/"
                qs = scope.get("query_string") or b""
                loc = "https://" + self.canonical + path
                if qs:
                    loc += "?" + qs.decode("latin1")
                body = b""
                headers = [
                    (b"location", loc.encode("latin1")),
                    (b"cache-control", b"no-store"),
                    (b"content-length", b"0"),
                ]
                await send({"type": "http.response.start", "status": 301, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


app.add_middleware(CanonicalHostMiddleware)

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.filters["fmt_time"] = fmt_time
templates.env.filters["fmt_bytes"] = dj_flac.fmt_bytes
templates.env.filters["epoch"] = lambda v: int((v if v.tzinfo else v.replace(tzinfo=timezone.utc)).timestamp()) if v else 0
templates.env.filters["chat_created_ms"] = chat_created_ms
templates.env.filters["fmt_date"] = fmt_date
templates.env.globals["track_categories"] = TRACK_CATEGORY_CHOICES
templates.env.globals["track_audio_url"] = track_audio_url
templates.env.globals["track_has_flac"] = track_has_flac
templates.env.globals["display_name"] = display_name
templates.env.globals["ann_media"] = ann_media
templates.env.globals["ann_links"] = ann_links
templates.env.globals["ann_links_of"] = lambda post: ann_links(post_links_obj(post))
templates.env.globals["chat_media"] = chat_media
templates.env.globals["chat_excerpt"] = chat_excerpt
templates.env.filters["linkify"] = linkify
templates.env.filters["tg_eng_line"] = tg_eng_line
templates.env.filters["cn_count"] = lambda n: format_cn_count(int(n or 0))


@app.exception_handler(MultiPartException)
async def handle_multipart_error(request: Request, exc: MultiPartException):
    try:
        items = request.session.get("flashes") or []
        items.append(
            {
                "message": "上传文件过大或表单无效，音乐包请不超过 200MB。",
                "category": "error",
            }
        )
        request.session["flashes"] = items
    except Exception:
        pass
    path = request.url.path or "/"
    if path.startswith("/settings"):
        target = "/settings"
    elif path.startswith("/admin") or path.startswith("/upload"):
        target = "/admin"
    else:
        target = "/"
    return RedirectResponse(target, status_code=303)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def require_csrf(request: Request, token: Optional[str]) -> None:
    expected = request.session.get("csrf")
    if (
        not expected
        or not token
        or not secrets.compare_digest(str(expected), str(token))
    ):
        raise HTTPException(status_code=400, detail="无效请求，请刷新页面后重试")


def establish_session(request: Request, user: User) -> None:
    """Mark this browser session as the current login for the user."""
    request.session["user_id"] = user.id
    request.session["session_ver"] = int(getattr(user, "session_ver", 0) or 0)


def current_user(request: Request, db: Session) -> Optional[User]:
    uid = request.session.get("user_id")
    if not uid:
        return None
    try:
        user = db.get(User, int(uid))
    except (TypeError, ValueError):
        return None
    if not user:
        return None
    ver = int(getattr(user, "session_ver", 0) or 0)
    sess_ver = request.session.get("session_ver")
    if sess_ver is None:
        # Pre-upgrade cookies: still valid only until the password is changed.
        if ver != 0:
            request.session.pop("user_id", None)
            request.session.pop("session_ver", None)
            return None
        return user
    try:
        if int(sess_ver) != ver:
            request.session.pop("user_id", None)
            request.session.pop("session_ver", None)
            return None
    except (TypeError, ValueError):
        request.session.pop("user_id", None)
        request.session.pop("session_ver", None)
        return None
    return user


def flash(request: Request, message: str, category: str = "info") -> None:
    items = request.session.get("flashes") or []
    items.append({"message": message, "category": category})
    request.session["flashes"] = items


ADMIN_BACKEND_TIP = "只有管理员可以进入后台。"


def _is_admin_backend_tip(message: str) -> bool:
    """Session flashes about entering /admin must never render on frontend pages."""
    text = (message or "").strip()
    return text == ADMIN_BACKEND_TIP or "进入后台" in text


def pop_flashes(request: Request) -> list:
    items = request.session.get("flashes") or []
    request.session["flashes"] = []
    # Drop leftover or mistaken admin-backend tips so they never appear on frontend pages.
    return [
        it for it in items
        if not _is_admin_backend_tip((it or {}).get("message") if isinstance(it, dict) else "")
    ]


def ctx(request: Request, db: Session, **extra):
    user = current_user(request, db)
    data = {
        "request": request,
        "user": user,
        "csrf": get_csrf(request),
        "flashes": pop_flashes(request),
    }
    data.update(extra)
    return data


def login_redirect(next_url: str = "/") -> RedirectResponse:
    target = "/login"
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        target = f"/login?next={next_url}"
    return RedirectResponse(target, status_code=303)


def safe_next_path(next_url: str, user: Optional[User] = None) -> str:
    """Allow only in-site relative paths. Admins with the default next=/ land on /admin."""
    target = next_url if next_url and next_url.startswith("/") and not next_url.startswith("//") else "/"
    if target.startswith("/admin") and not (user and user.is_admin):
        return "/"
    if user and user.is_admin and target in {"/", "/login", "/register", "/home"}:
        return "/admin"
    return target



def ann_day_bounds_utc(day: str) -> Optional[tuple[datetime, datetime]]:
    """Interpret YYYY-MM-DD as a calendar day in 东南亚DJ时间 (UTC+8)."""
    try:
        d = datetime.strptime((day or "").strip(), "%Y-%m-%d")
    except ValueError:
        return None
    start = datetime(d.year, d.month, d.day, tzinfo=TZ_CN).astimezone(timezone.utc).replace(tzinfo=None)
    return start, start + timedelta(days=1)


def announcements_filtered(q: str = "", day: str = "", include_hidden: bool = False):
    stmt = select(Announcement)
    if not include_hidden:
        stmt = stmt.where(or_(Announcement.hidden == False, Announcement.hidden.is_(None)))  # noqa: E712
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        stmt = stmt.where(
            or_(
                Announcement.title.ilike(like),
                Announcement.content.ilike(like),
                Announcement.links_json.ilike(like),
            )
        )
    bounds = ann_day_bounds_utc(day)
    if bounds:
        start, end = bounds
        stmt = stmt.where(Announcement.created_at >= start, Announcement.created_at < end)
    return stmt.order_by(Announcement.created_at.desc(), Announcement.id.desc())


def paginate_announcements(
    db: Session,
    *,
    q: str = "",
    day: str = "",
    page: int = 1,
    page_size: int = ANN_PAGE_SIZE,
    highlight_id: Optional[int] = None,
    include_hidden: bool = False,
) -> tuple[list, int, int, int]:
    stmt = announcements_filtered(q, day, include_hidden=include_hidden)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page_size = max(1, page_size)
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    if highlight_id and not q and not day and total:
        id_stmt = select(Announcement.id).order_by(Announcement.created_at.desc(), Announcement.id.desc())
        if not include_hidden:
            id_stmt = id_stmt.where(or_(Announcement.hidden == False, Announcement.hidden.is_(None)))  # noqa: E712
        ids = list(db.scalars(id_stmt).all())
        try:
            page = ids.index(int(highlight_id)) // page_size + 1
        except ValueError:
            pass
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = min(max(1, page), pages)
    rows = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all())
    return rows, total, page, pages


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    q = (request.query_params.get("q") or "").strip()
    day = (request.query_params.get("date") or "").strip()
    try:
        page = int(request.query_params.get("page") or 1)
    except ValueError:
        page = 1
    highlight = request.query_params.get("ann")
    try:
        highlight_id = int(highlight) if highlight else None
    except ValueError:
        highlight_id = None
    announcements, ann_total, ann_page, ann_pages = paginate_announcements(
        db, q=q, day=day, page=page, page_size=ANN_PAGE_SIZE, highlight_id=highlight_id
    )
    counted = count_announcement_views(request, db, [a.id for a in announcements])
    for a in announcements:
        if a.id in counted:
            # The batch UPDATE ran in SQL; reflect it on the loaded rows without marking them dirty.
            set_committed_value(a, "site_views", int(a.site_views or 0) + 1)
    ann_stats = ann_interactions(db, [a.id for a in announcements], current_user(request, db), announcements)
    return templates.TemplateResponse(
        request,
        "index.html",
        ctx(
            request,
            db,
            announcements=announcements,
            ann_stats=ann_stats,
            ann_comment_max=ANN_COMMENT_MAX_LEN,
            ann_q=q,
            ann_date=day,
            ann_total=ann_total,
            ann_page=ann_page,
            ann_pages=ann_pages,
            ann_page_size=ANN_PAGE_SIZE,
            nav="home",
            **hero_copy_public(db),
        ),
    )


@app.get("/home")
def home_alias():
    return RedirectResponse("/", status_code=303)


@app.get("/music", response_class=HTMLResponse)
def music_page(request: Request, db: Session = Depends(get_db)):
    tracks = approved_tracks(db)
    return templates.TemplateResponse(request, "music.html", ctx(request, db, tracks=tracks, nav="music"))


def dj_mixer_ctx(db: Session, user=None) -> dict:
    tracks = approved_tracks(db)
    library = [
        {"id": t.id, "title": t.title or t.original_name or f"曲目 {t.id}", "url": track_audio_url(t)}
        for t in tracks
    ]
    is_admin = bool(user and getattr(user, "is_admin", False))
    opt_cfg = {
        "maxMB": _opt_max_bytes(is_admin) // (1024 * 1024),
        "maxHours": _opt_max_duration(is_admin) // 3600,
        "exts": sorted(OPT_EXTS),
        "targets": list(OPT_TARGETS.keys()),
        "defaultTarget": OPT_DEFAULT_TARGET,
        "isAdmin": is_admin,
        "keepFlacDefault": is_admin,
    }
    return {"library": library, "opt_cfg": opt_cfg}


@app.get("/dj", response_class=HTMLResponse)
def dj_mixer_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "dj.html", ctx(request, db, nav="dj", **dj_mixer_ctx(db, current_user(request, db)))
    )


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, db: Session = Depends(get_db)):
    messages = db.scalars(
        select(ChatMessage)
        .options(joinedload(ChatMessage.user))
        .order_by(ChatMessage.created_at.desc())
        .limit(CHAT_LIMIT)
    ).unique().all()
    messages = list(reversed(messages))
    replies = chat_reply_info(db, messages)
    return templates.TemplateResponse(request, "chat.html", ctx(request, db, messages=messages, replies=replies, chat_max_len=CHAT_MAX_LEN, nav="chat"))


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)):
    if current_user(request, db):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "register.html", ctx(request, db))


@app.post("/register")
def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    agree: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    if current_user(request, db):
        return RedirectResponse("/", status_code=303)
    if str(agree).strip().lower() not in {"1", "on", "true", "yes"}:
        flash(request, "请先阅读并同意《平台合约规则》。", "error")
        return RedirectResponse("/register", status_code=303)
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        flash(request, "请输入有效的邮箱地址。", "error")
        return RedirectResponse("/register", status_code=303)
    if len(password) < PASSWORD_MIN:
        flash(request, f"密码至少需要 {PASSWORD_MIN} 位。", "error")
        return RedirectResponse("/register", status_code=303)
    if password != password2:
        flash(request, "两次输入的密码不一致。", "error")
        return RedirectResponse("/register", status_code=303)
    exists = db.scalar(select(User).where(User.email == email))
    if exists:
        flash(request, "该邮箱已经注册，请直接登录。", "error")
        return RedirectResponse("/register", status_code=303)
    username = allocate_username(db, default_username_seed(email, is_admin=False))
    user = User(
        email=email,
        username=username,
        password_hash=hash_password(password),
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    establish_session(request, user)
    flash(request, "注册成功，欢迎来到 DJ666！", "success")
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", db: Session = Depends(get_db)):
    user = current_user(request, db)
    if user:
        return RedirectResponse(safe_next_path(next or "/", user), status_code=303)
    return templates.TemplateResponse(request, "login.html", ctx(request, db, next_url=next or "/"))


@app.post("/login")
def login_submit(
    request: Request,
    password: str = Form(...),
    account: str = Form(""),
    email: str = Form(""),
    next: str = Form("/"),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    ident = (account or email).strip()
    user = None
    if "@" in ident:
        user = db.scalar(select(User).where(User.email == ident.lower()))
    elif ident:
        user = db.scalar(select(User).where(func.lower(User.username) == ident.lower()))
    if not user or not verify_password(password, user.password_hash):
        flash(request, "账号或密码不正确。", "error")
        return RedirectResponse("/login", status_code=303)
    establish_session(request, user)
    flash(request, "登录成功。", "success")
    return RedirectResponse(safe_next_path(next, user), status_code=303)


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    # Always end the session. A stale CSRF (other tab already logged out, or an
    # older page still showing 退出) must not pop 「无效请求」 and leave the
    # user stuck. GET /logout already clears without a token.
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
def logout_get(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "rules.html", ctx(request, db))


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return login_redirect("/settings")
    my_tracks = db.scalars(
        select(Track)
        .where(Track.uploader_id == user.id)
        .order_by(Track.created_at.desc())
    ).all()
    return templates.TemplateResponse(
        request,
        "settings.html",
        ctx(
            request,
            db,
            my_tracks=my_tracks,
            dup_batches=load_dup_batches(user),
            contact=get_submission_contact(db),
            nav="settings",
        ),
    )


@app.post("/settings/username")
def settings_username(
    request: Request,
    username: str = Form(...),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = current_user(request, db)
    if not user:
        return login_redirect("/settings")
    error = validate_username(username)
    if error:
        flash(request, error, "error")
        return RedirectResponse("/settings", status_code=303)
    name = username.strip()
    taken = db.scalar(select(User).where(User.username == name, User.id != user.id))
    if taken:
        flash(request, "该用户名已被使用，请换一个。", "error")
        return RedirectResponse("/settings", status_code=303)
    user.username = name
    db.commit()
    flash(request, "用户名已更新。", "success")
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/password")
def settings_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password2: str = Form(...),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = current_user(request, db)
    if not user:
        return login_redirect("/settings")
    if not verify_password(current_password, user.password_hash):
        flash(request, "当前密码不正确。", "error")
        return RedirectResponse("/settings", status_code=303)
    if len(new_password) < PASSWORD_MIN:
        flash(request, f"新密码至少需要 {PASSWORD_MIN} 位。", "error")
        return RedirectResponse("/settings", status_code=303)
    if new_password != new_password2:
        flash(request, "两次输入的新密码不一致。", "error")
        return RedirectResponse("/settings", status_code=303)
    if verify_password(new_password, user.password_hash):
        flash(request, "新密码不能与当前密码相同。", "error")
        return RedirectResponse("/settings", status_code=303)
    user.password_hash = hash_password(new_password)
    db.commit()
    flash(request, "登录密码已更新。", "success")
    return RedirectResponse("/settings", status_code=303)



@app.post("/settings/upload")
async def settings_upload_pack(
    request: Request,
    csrf_token: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = current_user(request, db)
    if not user:
        return login_redirect("/settings")
    original = Path(file.filename or "").name
    ext = Path(original).suffix.lower()
    if ext in ALLOWED_EXTS:
        flash(
            request,
            "请上传音乐包（ZIP / RAR / 7Z 压缩包），不能直接上传单个音频文件。",
            "error",
        )
        return RedirectResponse("/settings#music", status_code=303)
    if ext not in PACK_EXTS:
        flash(request, "仅支持 ZIP、RAR 或 7Z 格式的音乐包。", "error")
        return RedirectResponse("/settings#music", status_code=303)
    if ext != ".zip" and not find_archive_tool(ext):
        flash(request, "服务器暂不支持该压缩格式，请改用 ZIP 音乐包上传。", "error")
        return RedirectResponse("/settings#music", status_code=303)

    tmp_root = Path(tempfile.mkdtemp(prefix="dj666_pack_"))
    archive_path = tmp_root / f"pack{ext}"
    extract_dir = tmp_root / "extracted"
    stored_files: list[str] = []
    new_pack_ids: list[int] = []
    try:
        size = await save_upload_limited(file, archive_path, MAX_PACK_BYTES)
        if size < 0:
            flash(request, "音乐包不能超过 200MB。", "error")
            return RedirectResponse("/settings#music", status_code=303)
        if size == 0:
            flash(request, "上传的音乐包是空的。", "error")
            return RedirectResponse("/settings#music", status_code=303)
        try:
            extract_archive(archive_path, extract_dir, ext)
        except ValueError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse("/settings#music", status_code=303)
        except Exception:
            flash(request, "音乐包解压失败，请确认压缩包完整后再试。", "error")
            return RedirectResponse("/settings#music", status_code=303)

        extracted_size = 0
        file_count = 0
        for path in extract_dir.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            file_count += 1
            extracted_size += path.stat().st_size
            if file_count > MAX_EXTRACTED_FILES or extracted_size > MAX_EXTRACTED_BYTES:
                flash(request, "音乐包解压后体积或文件数量过大。", "error")
                return RedirectResponse("/settings#music", status_code=303)

        added = 0
        skipped_large = 0
        skipped_empty = 0
        auto_approve = bool(user.is_admin)
        status = TRACK_STATUS_APPROVED if auto_approve else TRACK_STATUS_PENDING
        pack_label = original[:255] if original else None
        seen_hashes: set[str] = set()
        skipped_inpack = 0
        staged: list[dict] = []
        stage_dir: Optional[Path] = None
        for src, rel, audio_ext in iter_pack_audio_files(extract_dir):
            if added + len(staged) >= MAX_TRACKS_PER_PACK:
                break
            src_size = src.stat().st_size
            if src_size <= 0:
                skipped_empty += 1
                continue
            if src_size > MAX_UPLOAD_BYTES:
                skipped_large += 1
                continue
            sha = file_sha256(src)
            if sha in seen_hashes:
                skipped_inpack += 1  # same audio twice inside this pack: keep the first
                continue
            seen_hashes.add(sha)
            entry_name = (src.name or rel.name)[:255]
            matches = find_duplicate_tracks(db, sha, src_size, entry_name)
            if matches:
                if stage_dir is None:
                    DUP_STAGING_DIR.mkdir(parents=True, exist_ok=True)
                    stage_dir = DUP_STAGING_DIR / secrets.token_urlsafe(18)
                    stage_dir.mkdir()
                staged_name = f"{len(staged)}{audio_ext}"
                shutil.copyfile(src, stage_dir / staged_name)
                staged.append(
                    {
                        "idx": len(staged),
                        "file": staged_name,
                        "ext": audio_ext,
                        "name": entry_name,
                        "size": src_size,
                        "sha": sha,
                        "matches": matches[:5],
                    }
                )
                continue
            stored = store_audio_file(src, audio_ext)
            if not stored:
                skipped_large += 1
                continue
            stored_files.append(stored)
            title = (src.stem or rel.stem or "未命名曲目").strip() or "未命名曲目"
            pack_track = Track(
                    title=title[:200],
                    filename=stored,
                    original_name=entry_name,
                    uploader_id=user.id,
                    status=status,
                    pack_name=pack_label,
                    content_hash=sha,
                    file_size=src_size,
                    category=infer_track_category(title, pack_label, entry_name),
                )
            db.add(pack_track)
            db.flush()
            new_pack_ids.append(pack_track.id)
            added += 1
        if staged and stage_dir is not None:
            (stage_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "user_id": user.id,
                        "created": datetime.now().timestamp(),
                        "pack_name": pack_label,
                        "items": staged,
                    },
                    ensure_ascii=False,
                ),
                "utf-8",
            )
            flash(
                request,
                f"音乐包中有 {len(staged)} 首与曲库已有曲目重复，已暂缓导入。请在下方「重复曲目待确认」中选择跳过或覆盖（不处理则默认跳过）。",
                "info",
            )
        if skipped_inpack:
            flash(request, f"音乐包内有 {skipped_inpack} 个内容完全相同的音频，只保留了第一个。", "info")
        if added == 0 and staged:
            return RedirectResponse("/settings#dupcheck", status_code=303)
        if added == 0:
            flash(
                request,
                "音乐包里没有找到可导入的音频文件（支持 mp3 / wav / ogg / m4a / flac）。"
                + (" 单个音频不能超过 50MB。" if skipped_large else ""),
                "error",
            )
            return RedirectResponse("/settings#music", status_code=303)
        db.commit()
        for tid in new_pack_ids:
            schedule_r2_upload(tid)
        extra = []
        if skipped_large:
            extra.append(f"跳过 {skipped_large} 个超过 50MB 的音频")
        if added == MAX_TRACKS_PER_PACK:
            extra.append(f"单次最多导入 {MAX_TRACKS_PER_PACK} 首")
        suffix = f"（{'，'.join(extra)}）" if extra else ""
        if auto_approve:
            flash(
                request,
                f"已从音乐包导入 {added} 首曲目，并发布到首页。{suffix}",
                "success",
            )
        else:
            flash(
                request,
                f"已从音乐包导入 {added} 首曲目，正在等待管理员审核，通过后才会出现在首页。{suffix}",
                "success",
            )
        return RedirectResponse("/settings#dupcheck" if staged else "/settings#music", status_code=303)
    except Exception:
        for name in stored_files:
            delete_track_file(name)
        if locals().get("stage_dir"):
            shutil.rmtree(locals()["stage_dir"], ignore_errors=True)
        db.rollback()
        flash(request, "上传失败，请稍后重试。", "error")
        return RedirectResponse("/settings#music", status_code=303)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


@app.post("/settings/upload/resolve")
async def settings_upload_resolve(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    require_csrf(request, form.get("csrf_token"))
    user = current_user(request, db)
    if not user:
        return login_redirect("/settings")
    token = str(form.get("token") or "")
    stage_dir = DUP_STAGING_DIR / token
    mf = stage_dir / "manifest.json"
    if not DUP_TOKEN_RE.match(token) or not mf.is_file():
        flash(request, "这批重复曲目已处理或已过期。", "error")
        return RedirectResponse("/settings#music", status_code=303)
    try:
        data = json.loads(mf.read_text("utf-8"))
    except Exception:
        data = {}
    if data.get("user_id") != user.id:
        flash(request, "这批重复曲目已处理或已过期。", "error")
        return RedirectResponse("/settings#music", status_code=303)
    skip_all = str(form.get("skip_all") or "") == "1"
    overwritten = 0
    skipped = 0
    failed = 0
    status_reset = 0
    try:
        for item in data.get("items", []):
            choice = "skip" if skip_all else str(form.get(f"act_{item.get('idx')}") or "skip")
            if not choice.startswith("overwrite:"):
                skipped += 1
                continue
            tid_raw = choice.split(":", 1)[1]
            allowed = {
                m["id"]
                for m in item.get("matches", [])
                if can_overwrite_track(user, m)
            }
            track = db.get(Track, int(tid_raw)) if tid_raw.isdigit() and int(tid_raw) in allowed else None
            src = stage_dir / Path(str(item.get("file") or "")).name
            ext = str(item.get("ext") or "").lower()
            if (
                not track
                or (not user.is_admin and track.uploader_id != user.id)
                or ext not in ALLOWED_EXTS
                or not src.is_file()
            ):
                failed += 1
                continue
            stored = store_audio_file(src, ext)
            if not stored:
                failed += 1
                continue
            before = track.status
            overwrite_track_audio(
                db, track, stored, str(item.get("name") or ""), src.stat().st_size, file_sha256(src), user
            )
            if before != track.status:
                status_reset += 1
            overwritten += 1
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
    parts = []
    if overwritten:
        parts.append(f"已覆盖 {overwritten} 首（保留原曲目名称）")
    if skipped:
        parts.append(f"已跳过 {skipped} 首")
    if failed:
        parts.append(f"{failed} 首无法覆盖（曲目已删除或无权限），已跳过")
    msg = "，".join(parts) or "已处理"
    if status_reset:
        msg += f"。其中 {status_reset} 首音频已更换，需重新等待管理员审核"
    flash(request, msg + "。", "success" if not failed else "info")
    return RedirectResponse("/settings#music", status_code=303)


@app.post("/settings/tracks/{track_id}/rename")
def settings_rename_track(
    track_id: int,
    request: Request,
    title: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    wants_json = request.headers.get("x-requested-with") == "dj666-rename"
    try:
        require_csrf(request, csrf_token)
    except HTTPException:
        if wants_json:
            return JSONResponse({"ok": False, "error": "页面已过期，请刷新后重试"}, status_code=400)
        raise
    user = current_user(request, db)
    if not user:
        if wants_json:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        return login_redirect("/settings")
    track = db.get(Track, track_id)
    if not track or (track.uploader_id != user.id and not user.is_admin):
        if wants_json:
            return JSONResponse({"ok": False, "error": "只能修改自己上传的曲目名称"}, status_code=403)
        flash(request, "不能修改这首曲目的名称。", "error")
        return RedirectResponse("/settings#music", status_code=303)
    display = (title or "").strip()
    if not display:
        if wants_json:
            return JSONResponse({"ok": False, "error": "曲目名称不能为空"}, status_code=400)
        flash(request, "曲目标题不能为空。", "error")
        return RedirectResponse("/settings#music", status_code=303)
    track.title = display[:200]
    db.commit()
    if wants_json:
        return JSONResponse({"ok": True, "id": track.id, "title": track.title})
    flash(request, "曲目标题已更新。", "success")
    return RedirectResponse("/settings#music", status_code=303)


MAX_UPLOAD_BATCH = 50  # audio files per admin batch request


async def save_admin_audio(
    upload,
    user: User,
    title: str = "",
    db: Optional[Session] = None,
    on_duplicate: str = "ask",
    target_id: Optional[int] = None,
    category: str = "",
) -> dict:
    """Store one admin-uploaded audio file as an approved track. Never raises.

    Duplicate handling (same SHA-256, or same filename + size as an existing
    track): "ask" (default) stores nothing and returns the matches so the page
    can offer 跳过 / 覆盖; "skip" stores nothing; "overwrite" replaces the audio
    of ``target_id`` (must be one of the matches) and keeps its row and title.
    """
    original = Path(getattr(upload, "filename", "") or "").name
    result = {"name": original[:255] or "未命名文件", "ok": False}
    ext = Path(original).suffix.lower()
    stored_name = ""
    try:
        if ext not in ALLOWED_EXTS:
            result["error"] = "格式不支持（仅 mp3 / wav / ogg / m4a / flac）"
            return result
        stored_name = unique_upload_name(ext)
        dest = UPLOAD_DIR / stored_name
        size, sha = await save_upload_hashed(upload, dest, MAX_UPLOAD_BYTES)
        if size < 0:
            stored_name = ""
            result["error"] = f"超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB"
            return result
        if size == 0:
            result["error"] = "文件是空的"
            return result
        try:
            dest.chmod(0o644)
        except OSError:
            pass
        matches = find_duplicate_tracks(db, sha, size, original)
        if matches:
            result["matches"] = matches[:5]
            if on_duplicate == "overwrite":
                target = next((m for m in matches if m["id"] == target_id), None) if target_id else matches[0]
                track = db.get(Track, target["id"]) if target else None
                if not track:
                    result["error"] = "要覆盖的曲目不存在或已不是重复项"
                    return result
                overwrite_track_audio(db, track, stored_name, original, size, sha, user)
                stored_name = ""
                result.update(ok=True, overwritten=True, id=track.id, title=track.title)
                return result
            result["duplicate"] = True
            result["skipped"] = on_duplicate == "skip"
            result["error"] = "与曲库中已有曲目重复，已跳过" if on_duplicate == "skip" else "与曲库中已有曲目重复"
            return result
        display = (title or "").strip() or Path(original).stem.strip() or "未命名曲目"
        cat = normalize_track_category(category) or infer_track_category(display, "", original)
        track = Track(
            title=display[:200],
            filename=stored_name,
            original_name=original[:255],
            uploader_id=user.id,
            status=TRACK_STATUS_APPROVED,
            pack_name=None,
            content_hash=sha,
            file_size=size,
            category=cat,
        )
        try:
            db.add(track)
            db.commit()
        except Exception:
            db.rollback()
            result["error"] = "保存失败"
            return result
        stored_name = ""
        result.update(ok=True, id=track.id, title=track.title)
        schedule_r2_upload(track.id)
        return result
    except Exception:
        db.rollback()
        result["error"] = "上传失败"
        return result
    finally:
        if stored_name:
            delete_track_file(stored_name)
        try:
            await upload.close()
        except Exception:
            pass


@app.post("/upload")
async def upload_track(request: Request, db: Session = Depends(get_db)):
    wants_json = request.headers.get("x-requested-with") == "dj666-upload"
    form = await request.form()
    uploads = [f for f in form.getlist("file") if hasattr(f, "filename") and getattr(f, "filename", "")]
    require_csrf(request, form.get("csrf_token"))
    user = current_user(request, db)
    if not user:
        if wants_json:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        return RedirectResponse("/admin", status_code=303)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="只有管理员可以上传音乐")
    if not uploads:
        if wants_json:
            return JSONResponse({"ok": False, "error": "请选择音频文件"}, status_code=400)
        flash(request, "请选择要上传的音频文件。", "error")
        return RedirectResponse("/admin#upload", status_code=303)
    overflow = uploads[MAX_UPLOAD_BATCH:]
    uploads = uploads[:MAX_UPLOAD_BATCH]
    # A custom title only applies when exactly one file is uploaded.
    title = str(form.get("title") or "") if len(uploads) == 1 and not overflow else ""
    on_dup = str(form.get("on_duplicate") or ("ask" if wants_json else "skip"))
    if on_dup not in ("ask", "skip", "overwrite"):
        on_dup = "ask"
    tid_raw = str(form.get("target_id") or "")
    target_id = int(tid_raw) if tid_raw.isdigit() else None
    if on_dup == "overwrite" and len(uploads) != 1:
        on_dup = "ask"
    category = str(form.get("category") or "")
    results = []
    for up in uploads:
        results.append(await save_admin_audio(up, user, title, db, on_dup, target_id, category))
    for up in overflow:
        try:
            await up.close()
        except Exception:
            pass
        results.append({"name": Path(up.filename or "").name[:255], "ok": False, "error": f"单次最多 {MAX_UPLOAD_BATCH} 个文件"})
    ok = [r for r in results if r["ok"]]
    dups = [r for r in results if r.get("duplicate")]
    bad = [r for r in results if not r["ok"] and not r.get("duplicate")]
    if wants_json:
        return JSONResponse({"ok": bool(ok), "saved": len(ok), "failed": len(bad), "results": results})
    if ok:
        if len(results) == 1:
            flash(request, "上传成功，已显示在首页音乐列表中。", "success")
        else:
            flash(request, f"已上传 {len(ok)} 首曲目，已显示在首页音乐列表中。", "success")
    if bad:
        detail = "；".join(f"{r['name']}：{r['error']}" for r in bad[:10])
        more = f" 等 {len(bad)} 个" if len(bad) > 10 else ""
        flash(request, f"{len(bad)} 个文件上传失败：{detail}{more}", "error")
    if dups:
        flash(request, f"{len(dups)} 个文件与曲库已有曲目重复，已跳过。", "info")
    return RedirectResponse("/admin#upload", status_code=303)


# ------------------------------------------------------------ Telegram bot
BOT_TOKEN_KEY = "tg_bot_token"
BOT_TOKEN_RE = re.compile(r"^\d{5,16}:[A-Za-z0-9_-]{30,60}$")


def _iso_fmt(raw: str) -> str:
    try:
        return fmt_time(datetime.fromisoformat(raw)) if raw else ""
    except ValueError:
        return ""


def bot_config(db: Session) -> dict:
    """Public view of the bot config for templates: never contains the token."""
    token = get_setting(db, BOT_TOKEN_KEY, "")
    return {
        "configured": bool(token),
        "masked": ("•" * 8 + token[-4:]) if token else "",
        "username": get_setting(db, "tg_bot_username", ""),
        "name": get_setting(db, "tg_bot_name", ""),
        "last_test": _iso_fmt(get_setting(db, "tg_bot_last_test", "")),
        "last_ok": get_setting(db, "tg_bot_last_ok", "") == "1",
        "last_error": get_setting(db, "tg_bot_last_error", ""),
        "saved_at": _iso_fmt(get_setting(db, "tg_bot_saved_at", "")),
        "webhook": get_setting(db, "tg_bot_webhook_ok", ""),
        "webhook_error": get_setting(db, "tg_bot_webhook_error", ""),
    }


def telegram_get_me(token: str) -> tuple[bool, dict, str]:
    """Call Bot API getMe. Error strings never include the token."""
    if not BOT_TOKEN_RE.match(token or ""):
        return False, {}, "Token 格式不正确"
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dj666-bot/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8", "replace"))
            desc = str(data.get("description") or "")
        except Exception:
            desc = ""
        if exc.code == 401:
            return False, {}, "Token 无效或已被撤销（401 Unauthorized）"
        if exc.code == 404:
            return False, {}, "Token 无效（404 Not Found）"
        return False, {}, f"Telegram 返回错误 {exc.code}" + (f"：{desc[:120]}" if desc and token not in desc else "")
    except Exception as exc:  # noqa: BLE001
        reason = getattr(exc, "reason", None) or exc.__class__.__name__
        msg = str(reason)
        if token in msg:
            msg = "网络错误"
        return False, {}, f"无法连接 Telegram 服务器（{msg[:80]}）"
    if not data.get("ok"):
        return False, {}, "Telegram 返回失败"
    return True, data.get("result") or {}, ""


def _bot_redirect() -> RedirectResponse:
    return RedirectResponse("/admin#botconfig", status_code=303)


def _bot_record_test(db: Session, ok: bool, info: dict, err: str) -> None:
    set_setting(db, "tg_bot_last_test", datetime.now(timezone.utc).isoformat())
    set_setting(db, "tg_bot_last_ok", "1" if ok else "0")
    set_setting(db, "tg_bot_last_error", "" if ok else err[:200])
    if ok:
        set_setting(db, "tg_bot_username", str(info.get("username") or "")[:64])
        set_setting(db, "tg_bot_name", str(info.get("first_name") or "")[:64])


@app.post("/admin/bot/save")
def admin_bot_save(
    request: Request,
    token: str = Form(""),
    then_test: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    value = (token or "").strip()
    if not value:
        flash(request, "请输入 Bot Token。留空不会修改已保存的 Token。", "error")
        return _bot_redirect()
    if not BOT_TOKEN_RE.match(value):
        flash(request, "Bot Token 格式不正确，应类似 123456789:AA…（从 @BotFather 获取）。", "error")
        return _bot_redirect()
    set_setting(db, BOT_TOKEN_KEY, value)
    set_setting(db, "tg_bot_saved_at", datetime.now(timezone.utc).isoformat())
    for key in ("tg_bot_username", "tg_bot_name", "tg_bot_last_test", "tg_bot_last_ok", "tg_bot_last_error"):
        set_setting(db, key, "")
    set_setting(db, "tg_bot_webhook_ok", "")
    wh_ok, wh_err = bot_register_webhook(db)
    if not wh_ok:
        flash(request, f"/start 自动回复（Webhook）设置失败：{wh_err}", "error")
    if then_test == "1":
        ok, info, err = telegram_get_me(value)
        _bot_record_test(db, ok, info, err)
        if ok:
            flash(request, f"Bot Token 已保存，连接测试成功：@{info.get('username', '')}", "success")
        else:
            flash(request, f"Bot Token 已保存，但连接测试失败：{err}", "error")
    else:
        flash(request, "Bot Token 已保存。", "success")
    return _bot_redirect()


@app.post("/admin/bot/test")
def admin_bot_test(request: Request, csrf_token: str = Form(""), db: Session = Depends(get_db)):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    token = get_setting(db, BOT_TOKEN_KEY, "")
    if not token:
        flash(request, "还没有保存 Bot Token，请先保存再测试。", "error")
        return _bot_redirect()
    ok, info, err = telegram_get_me(token)
    _bot_record_test(db, ok, info, err)
    if ok:
        wh_ok, wh_err = bot_register_webhook(db)
        if not wh_ok:
            flash(request, f"/start 自动回复（Webhook）设置失败：{wh_err}", "error")
        flash(request, f"连接成功：机器人 @{info.get('username', '')}（{info.get('first_name', '')}）可以正常使用。", "success")
    else:
        flash(request, f"连接测试失败：{err}", "error")
    return _bot_redirect()


@app.post("/admin/bot/clear")
def admin_bot_clear(request: Request, csrf_token: str = Form(""), db: Session = Depends(get_db)):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    old_token = get_setting(db, BOT_TOKEN_KEY, "")
    if old_token:
        telegram_api(old_token, "deleteWebhook", {}, timeout=10)
    for key in (BOT_TOKEN_KEY, "tg_bot_saved_at", "tg_bot_username", "tg_bot_name", "tg_bot_last_test", "tg_bot_last_ok", "tg_bot_last_error", "tg_bot_webhook_ok", "tg_bot_webhook_error"):
        set_setting(db, key, "")
    flash(request, "Bot Token 已删除。", "success")
    return _bot_redirect()



# --- 机器人配置: 广告图片 + 内联按钮 (stored for later push; nothing is sent yet) ---
BOT_ADS_DIR = UPLOAD_DIR / "botads"          # not under any public route; served to admins only
BOT_ADS_KEY = "tg_bot_ads"
BOT_BUTTONS_KEY = "tg_bot_buttons"
BOT_AD_MAX_BYTES = 10 * 1024 * 1024          # Telegram sendPhoto limit is 10 MB
BOT_AD_MAX_COUNT = 50
BOT_AD_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(?:jpg|jpeg|png|gif|webp)$")
BOT_BTN_MAX = 30
BOT_BTN_PER_ROW = 8
BOT_BTN_TEXT_MAX = 64
BOT_BTN_URL_MAX = 500


def _json_setting(db: Session, key: str) -> list:
    try:
        val = json.loads(get_setting(db, key, "") or "[]")
        return val if isinstance(val, list) else []
    except ValueError:
        return []


def bot_ads(db: Session) -> list[dict]:
    out = []
    for item in _json_setting(db, BOT_ADS_KEY):
        if not isinstance(item, dict) or not BOT_AD_NAME_RE.match(str(item.get("file") or "")):
            continue
        out.append({
            "file": item["file"],
            "name": str(item.get("name") or "")[:120],
            "size": int(item.get("size") or 0),
            "w": int(item.get("w") or 0),
            "h": int(item.get("h") or 0),
            "uploaded": _iso_fmt(str(item.get("uploaded") or "")),
            "url": f"/admin/bot/ads/{item['file']}",
        })
    return out


def bot_ad_paths(db: Session) -> list[Path]:
    """Saved ad image files on disk (for a future sendPhoto push), in upload order."""
    return [p for p in (BOT_ADS_DIR / a["file"] for a in bot_ads(db)) if p.is_file()]


# Telegram sendPhoto: keep in-chat photo display large. Longest side ~1280px is a
# well-supported large size; originals already at/above that are sent as-is.
# Width+height must stay <= 10000, aspect ratio <= 20, file <= 10 MB.
BOT_PHOTO_TARGET = 1280
BOT_PHOTO_ALREADY_LARGE = 1200  # already a Telegram-friendly large size; send original as-is
BOT_PHOTO_MAX_SIDE_SUM = 10000
BOT_PHOTO_MAX_RATIO = 20


def bot_photo_payload(path: Path) -> tuple[str, bytes]:
    """(filename, bytes) for sendPhoto. Upscale small images for large in-chat display.
    Never overwrites the admin's original file; derived bytes are used only for sending.
    """
    raw = path.read_bytes()
    name = path.name
    if PILImage is None or not raw:
        return name, raw
    from io import BytesIO
    try:
        with PILImage.open(BytesIO(raw)) as im:
            frames = getattr(im, "n_frames", 1) or 1
            if frames > 1:
                return name, raw
            w, h = im.size
            if w < 1 or h < 1:
                return name, raw
            tw, th = w, h
            need = False
            long_side = max(w, h)
            if long_side < BOT_PHOTO_ALREADY_LARGE:
                scale = BOT_PHOTO_TARGET / float(long_side)
                tw = max(1, int(round(w * scale)))
                th = max(1, int(round(h * scale)))
                need = True
            if tw + th > BOT_PHOTO_MAX_SIDE_SUM:
                s = BOT_PHOTO_MAX_SIDE_SUM / float(tw + th)
                tw = max(1, int(tw * s))
                th = max(1, int(th * s))
                need = True
            ratio = max(tw, th) / float(max(1, min(tw, th)))
            if ratio > BOT_PHOTO_MAX_RATIO:
                if tw >= th:
                    tw = max(1, int(th * BOT_PHOTO_MAX_RATIO))
                else:
                    th = max(1, int(tw * BOT_PHOTO_MAX_RATIO))
                need = True
            if not need:
                return name, raw
            work = im
            if work.mode != "RGB":
                if work.mode in ("RGBA", "LA") or (work.mode == "P" and "transparency" in work.info):
                    rgba = work.convert("RGBA")
                    bg = PILImage.new("RGB", rgba.size, (0, 0, 0))
                    bg.paste(rgba, mask=rgba.split()[-1])
                    work = bg
                else:
                    work = work.convert("RGB")
            else:
                work = work.copy()
            work = work.resize((tw, th), PILImage.LANCZOS)
        buf = BytesIO()
        work.save(buf, format="JPEG", quality=90, optimize=True)
        data = buf.getvalue()
        if len(data) > BOT_AD_MAX_BYTES:
            buf = BytesIO()
            work.save(buf, format="JPEG", quality=75, optimize=True)
            data = buf.getvalue()
        if len(data) > BOT_AD_MAX_BYTES:
            return name, raw
        return (path.stem + ".jpg"), data
    except Exception:  # noqa: BLE001
        return name, raw


def bot_safe_url(raw: str) -> str:
    """Only absolute http(s) URLs with a host (t.me links are https); '' if unsafe."""
    url = (raw or "").strip()
    if not url or len(url) > BOT_BTN_URL_MAX or any(ord(ch) < 33 for ch in url):
        return ""
    if re.match(r"^(t\.me|telegram\.me)/", url, re.I):
        url = "https://" + url
    m = re.match(r"^(https?)://([^/?#\s]+)", url, re.I)
    if not m or "@" in m.group(2) or not re.match(r"^[A-Za-z0-9.-]+(:\d{1,5})?$", m.group(2)) or "." not in m.group(2):
        return ""
    return url


def bot_buttons(db: Session) -> list[dict]:
    out = []
    for item in _json_setting(db, BOT_BUTTONS_KEY):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()[:BOT_BTN_TEXT_MAX]
        url = bot_safe_url(str(item.get("url") or ""))
        if text and url:
            out.append({"text": text, "url": url, "same_row": bool(item.get("same_row")) and bool(out)})
    return out


def bot_keyboard_rows(buttons: list[dict]) -> list[list[dict]]:
    """Group buttons into Telegram inline_keyboard rows (max 8 per row).

    Telegram cannot change inline-button font or height. One button per row
    makes each control full width and easier to tap. Buttons stay on the same
    row only when the admin explicitly chose 「接上一行」(same_row).
    """
    rows: list[list[dict]] = []
    for b in buttons:
        if rows and b.get("same_row") and len(rows[-1]) < BOT_BTN_PER_ROW:
            rows[-1].append(b)
        else:
            rows.append([b])
    return rows


def bot_inline_keyboard(db: Session) -> dict:
    """reply_markup payload for sendPhoto/sendMessage (used by the /start reply)."""
    return {"inline_keyboard": [[{"text": b["text"], "url": b["url"]} for b in row] for row in bot_keyboard_rows(bot_buttons(db))]}


def _botads_redirect() -> RedirectResponse:
    return RedirectResponse("/admin#botads", status_code=303)


@app.post("/admin/bot/ads/upload")
async def admin_bot_ads_upload(
    request: Request,
    csrf_token: str = Form(""),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    ads = _json_setting(db, BOT_ADS_KEY)
    BOT_ADS_DIR.mkdir(parents=True, exist_ok=True)
    added, bad = 0, []
    for up in files or []:
        if not (up.filename or "").strip():
            await up.close(); continue
        orig = Path(up.filename).name[:120] or "image"
        ext = Path(orig).suffix.lower()
        if ext not in ANN_IMAGE_EXTS:
            bad.append(f"{orig}（只支持 jpg / png / gif / webp）"); await up.close(); continue
        if len(ads) >= BOT_AD_MAX_COUNT:
            bad.append(f"{orig}（最多保存 {BOT_AD_MAX_COUNT} 张）"); await up.close(); continue
        name = f"{uuid.uuid4().hex}{ext}"
        dest = BOT_ADS_DIR / name
        size = await save_upload_limited(up, dest, BOT_AD_MAX_BYTES)
        if size < 0:
            bad.append(f"{orig}（超过 10MB）"); continue
        if size == 0 or not sniff_media_ok(dest, ext):
            dest.unlink(missing_ok=True); bad.append(f"{orig}（不是有效的图片）"); continue
        w = h = 0
        if PILImage is not None:
            try:
                with PILImage.open(dest) as im:
                    im.verify()
                with PILImage.open(dest) as im:
                    w, h = im.size
            except Exception:  # noqa: BLE001
                dest.unlink(missing_ok=True); bad.append(f"{orig}（图片已损坏）"); continue
        ads.append({"file": name, "name": orig, "size": size, "w": w, "h": h,
                    "uploaded": datetime.now(timezone.utc).isoformat()})
        added += 1
    set_setting(db, BOT_ADS_KEY, json.dumps(ads, ensure_ascii=False))
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"ok": bool(added), "added": added, "bad": bad, "ads": bot_ads(db),
                             "max": BOT_AD_MAX_COUNT}, headers={"Cache-Control": "no-store"})
    if added:
        flash(request, f"已上传 {added} 张广告图片。", "success")
    if bad:
        flash(request, "以下文件未上传：" + "；".join(bad[:5]) + (f" 等 {len(bad)} 个" if len(bad) > 5 else ""), "error")
    if not added and not bad:
        flash(request, "请选择要上传的图片。", "error")
    return _botads_redirect()


@app.post("/admin/bot/ads/{filename}/delete")
def admin_bot_ads_delete(filename: str, request: Request, csrf_token: str = Form(""), db: Session = Depends(get_db)):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    ads = _json_setting(db, BOT_ADS_KEY)
    keep = [a for a in ads if not (isinstance(a, dict) and a.get("file") == filename)]
    if len(keep) == len(ads) or not BOT_AD_NAME_RE.match(filename or ""):
        flash(request, "图片不存在或已删除。", "error")
        return _botads_redirect()
    set_setting(db, BOT_ADS_KEY, json.dumps(keep, ensure_ascii=False))
    (BOT_ADS_DIR / filename).unlink(missing_ok=True)
    flash(request, "广告图片已删除。", "success")
    return _botads_redirect()


@app.get("/admin/bot/ads/{filename}")
def admin_bot_ads_file(filename: str, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or not user.is_admin or not BOT_AD_NAME_RE.match(filename or ""):
        raise HTTPException(status_code=404, detail="文件不存在")
    path = BOT_ADS_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path, media_type=ANN_MEDIA_MIME.get(path.suffix.lower(), "application/octet-stream"),
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@app.post("/admin/bot/buttons")
async def admin_bot_buttons_save(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    require_admin(request, db)
    texts = [str(x) for x in form.getlist("btn_text")]
    urls = [str(x) for x in form.getlist("btn_url")]
    rows = [str(x) for x in form.getlist("btn_row")]
    if len(texts) != len(urls):
        flash(request, "按钮数据不完整，请重试。", "error")
        return _botbtn_redirect()
    out, errors = [], []
    for i, (t, u) in enumerate(zip(texts, urls)):
        t, u = t.strip(), u.strip()
        if not t and not u:
            continue
        if not t:
            errors.append(f"第 {i + 1} 行缺少按钮文字"); continue
        if len(t) > BOT_BTN_TEXT_MAX:
            errors.append(f"第 {i + 1} 行按钮文字超过 {BOT_BTN_TEXT_MAX} 字"); continue
        safe = bot_safe_url(u)
        if not safe:
            errors.append(f"第 {i + 1} 行链接无效（只支持 http:// 或 https:// 开头的网址，如 https://t.me/xxx）"); continue
        same = (rows[i] if i < len(rows) else "") == "same" and bool(out)
        out.append({"text": t, "url": safe, "same_row": same})
    if errors:
        flash(request, "内联按钮未保存：" + "；".join(errors[:5]), "error")
        return _botbtn_redirect()
    if len(out) > BOT_BTN_MAX:
        flash(request, f"最多 {BOT_BTN_MAX} 个按钮。", "error")
        return _botbtn_redirect()
    over = [r for r in bot_keyboard_rows(out) if len(r) > BOT_BTN_PER_ROW]
    if over:
        flash(request, f"每行最多 {BOT_BTN_PER_ROW} 个按钮。", "error")
        return _botbtn_redirect()
    set_setting(db, BOT_BUTTONS_KEY, json.dumps(out, ensure_ascii=False))
    flash(request, f"内联按钮已保存（{len(out)} 个）。" if out else "已清空内联按钮。", "success")
    return _botbtn_redirect()



# --- 机器人 /start: Telegram webhook → 广告图片 + 内联按钮 ---
BOT_PUBLIC_BASE = os.environ.get("PUBLIC_BASE_URL", "https://www.dj666.pw").rstrip("/")
BOT_WEBHOOK_PATH = "/telegram/webhook"
BOT_WEBHOOK_SECRET_KEY = "tg_bot_webhook_secret"
BOT_START_MAX_PHOTOS = 10            # first photo carries the keyboard; up to 9 more follow as an album
BOT_UPDATE_STALE_SECS = 600          # ignore /start older than 10 min (e.g. queued while the server was down)
BOT_START_DEFAULT_TEXT = "🎧 欢迎来到 DJ666！\n管理员还没有配置广告图片和按钮，敬请期待。\n网站：https://www.dj666.pw"
BOT_START_BUTTONS_TEXT = "🎧 欢迎来到 DJ666！点击下方按钮了解更多 👇"
_bot_seen_updates: "dict[int, float]" = {}
_bot_seen_lock = threading.Lock()


def _bot_log(msg: str, token: str = "") -> None:
    if token:
        msg = msg.replace(token, "<token>")
    print(f"[tg-bot] {msg}", flush=True)


def telegram_api(token: str, method: str, fields: Optional[dict] = None,
                 files: Optional[list] = None, timeout: int = 30) -> tuple[bool, dict, str]:
    """Bot API call. files: [(field, filename, bytes)] → multipart upload. Errors never include the token."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    fields = dict(fields or {})
    if files:
        # multipart values are strings; JSON objects are ASCII-escaped so non-ASCII text survives any decoder
        fields = {k: (v if isinstance(v, str) else json.dumps(v, ensure_ascii=True)) for k, v in fields.items()}
        boundary = "----dj666" + uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode() + v.encode("utf-8") + b"\r\n")
        for field, fname, data in files:
            ctype = ANN_MEDIA_MIME.get(Path(fname).suffix.lower(), "application/octet-stream")
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{fname}"\r\n'
                         f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    else:
        body = json.dumps(fields, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
    headers["User-Agent"] = "dj666-bot/1.0"
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            data = {}
        desc = str(data.get("description") or f"HTTP {exc.code}").replace(token, "<token>")
        return False, data, desc[:200]
    except Exception as exc:  # noqa: BLE001
        return False, {}, str(getattr(exc, "reason", None) or exc.__class__.__name__).replace(token, "<token>")[:120]
    if not data.get("ok"):
        return False, data, str(data.get("description") or "Telegram 返回失败").replace(token, "<token>")[:200]
    return True, data, ""


def bot_webhook_secret(db: Session) -> str:
    sec = get_setting(db, BOT_WEBHOOK_SECRET_KEY, "")
    if not re.match(r"^[A-Za-z0-9_-]{32,256}$", sec or ""):
        sec = secrets.token_urlsafe(32)
        set_setting(db, BOT_WEBHOOK_SECRET_KEY, sec)
    return sec


def bot_register_webhook(db: Session) -> tuple[bool, str]:
    """setWebhook to the public HTTPS URL with a secret_token (not the bot token). Records the result."""
    token = get_setting(db, BOT_TOKEN_KEY, "")
    if not token:
        return False, "未设置 Token"
    ok, _, err = telegram_api(token, "setWebhook", {
        "url": BOT_PUBLIC_BASE + BOT_WEBHOOK_PATH,
        "secret_token": bot_webhook_secret(db),
        "allowed_updates": ["message"],
        "max_connections": 10,
    }, timeout=20)
    set_setting(db, "tg_bot_webhook_ok", "1" if ok else "0")
    set_setting(db, "tg_bot_webhook_error", "" if ok else err[:200])
    set_setting(db, "tg_bot_webhook_at", datetime.now(timezone.utc).isoformat())
    _bot_log("setWebhook " + ("ok" if ok else f"failed: {err}"), token)
    return ok, err


def bot_start_reply(db: Session, token: str, chat_id: int) -> list[tuple[str, bool, str]]:
    """Send the configured /start push. Returns [(method, ok, err)].
    Rules: image(s)+buttons → 1st image with the inline keyboard, then the other images (max 9) as an album;
    image(s) only → same without keyboard; buttons only → text + keyboard; nothing → default welcome text."""
    kb = bot_inline_keyboard(db)
    has_kb = bool(kb["inline_keyboard"])
    photos = bot_ad_paths(db)[:BOT_START_MAX_PHOTOS]
    results = []
    if photos:
        payloads = [bot_photo_payload(p) for p in photos]
        fields = {"chat_id": str(chat_id)}
        if has_kb:
            fields["reply_markup"] = kb
        fname, data = payloads[0]
        ok, _, err = telegram_api(token, "sendPhoto", fields, [("photo", fname, data)], timeout=60)
        results.append(("sendPhoto", ok, err))
        rest = payloads[1:]
        if len(rest) == 1:
            fname, data = rest[0]
            ok, _, err = telegram_api(token, "sendPhoto", {"chat_id": str(chat_id)}, [("photo", fname, data)], timeout=60)
            results.append(("sendPhoto", ok, err))
        elif rest:
            media = [{"type": "photo", "media": f"attach://p{i}"} for i in range(len(rest))]
            ok, _, err = telegram_api(token, "sendMediaGroup", {"chat_id": str(chat_id), "media": media},
                                      [(f"p{i}", fname, data) for i, (fname, data) in enumerate(rest)], timeout=120)
            results.append(("sendMediaGroup", ok, err))
    elif has_kb:
        ok, _, err = telegram_api(token, "sendMessage", {"chat_id": chat_id, "text": BOT_START_BUTTONS_TEXT,
                                                         "reply_markup": kb, "disable_web_page_preview": True})
        results.append(("sendMessage", ok, err))
    else:
        ok, _, err = telegram_api(token, "sendMessage", {"chat_id": chat_id, "text": BOT_START_DEFAULT_TEXT,
                                                         "disable_web_page_preview": True})
        results.append(("sendMessage", ok, err))
    return results


def bot_handle_update(update: dict) -> list:
    msg = update.get("message") or {}
    text = str(msg.get("text") or "").strip()
    cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
    chat_id = (msg.get("chat") or {}).get("id")
    if cmd != "/start" or not isinstance(chat_id, int):
        return []
    if time.time() - int(msg.get("date") or 0) > BOT_UPDATE_STALE_SECS:
        _bot_log(f"skip stale /start update {update.get('update_id')}")
        return []
    db = SessionLocal()
    try:
        token = get_setting(db, BOT_TOKEN_KEY, "")
        if not token:
            return []
        results = bot_start_reply(db, token, chat_id)
        _bot_log(f"/start update {update.get('update_id')}: " + ", ".join(f"{m} {'ok' if ok else 'FAIL ' + err}" for m, ok, err in results), token)
        return results
    except Exception as exc:  # noqa: BLE001
        _bot_log(f"/start handler error: {exc.__class__.__name__}")
        return []
    finally:
        db.close()


@app.post(BOT_WEBHOOK_PATH)
async def telegram_webhook(request: Request, db: Session = Depends(get_db)):
    expected = get_setting(db, BOT_WEBHOOK_SECRET_KEY, "")
    got = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not expected or not got or not secrets.compare_digest(expected, got):
        raise HTTPException(status_code=403, detail="forbidden")
    if not get_setting(db, BOT_TOKEN_KEY, ""):
        return {"ok": True}
    try:
        update = json.loads(await request.body())
    except ValueError:
        return {"ok": True}
    if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
        return {"ok": True}
    uid = update["update_id"]
    now = time.time()
    with _bot_seen_lock:
        if uid in _bot_seen_updates:
            return {"ok": True}
        _bot_seen_updates[uid] = now
        if len(_bot_seen_updates) > 2000:
            for k in [k for k, t in _bot_seen_updates.items() if now - t > 3600] or list(_bot_seen_updates)[:1000]:
                _bot_seen_updates.pop(k, None)
    asyncio.get_running_loop().run_in_executor(None, bot_handle_update, update)
    return {"ok": True}


def _bot_webhook_on_startup() -> None:
    db = SessionLocal()
    try:
        if get_setting(db, BOT_TOKEN_KEY, ""):
            bot_register_webhook(db)
    except Exception as exc:  # noqa: BLE001
        _bot_log(f"startup setWebhook error: {exc.__class__.__name__}")
    finally:
        db.close()


def _botbtn_redirect() -> RedirectResponse:
    return RedirectResponse("/admin#botbuttons", status_code=303)


@app.post("/admin/contact")
def admin_contact_save(
    request: Request,
    name: str = Form(""),
    telegram: str = Form(""),
    email: str = Form(""),
    note: str = Form(""),
    clear: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    if clear == "1":
        for key in CONTACT_KEYS:
            set_setting(db, key, "")
        flash(request, "投稿联系人已清空。", "success")
        return RedirectResponse("/admin#contact", status_code=303)
    tg, err = normalize_telegram(telegram)
    mail = (email or "").strip()
    if not err and mail and (len(mail) > 120 or not CONTACT_EMAIL_RE.match(mail)):
        err = "邮箱格式不正确。"
    if err:
        flash(request, err, "error")
        return RedirectResponse("/admin#contact", status_code=303)
    set_setting(db, "contact_name", (name or "").strip()[:40])
    set_setting(db, "contact_telegram", tg)
    set_setting(db, "contact_email", mail)
    set_setting(db, "contact_note", (note or "").strip()[:200])
    flash(request, "投稿联系人已保存，会显示在用户的「个人设置」页面。", "success")
    return RedirectResponse("/admin#contact", status_code=303)


@app.post("/admin/homecopy")
def admin_homecopy_save(
    request: Request,
    eyebrow: str = Form(""),
    title: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    set_setting(db, "hero_eyebrow", (eyebrow or "").strip()[:HERO_EYEBROW_MAX])
    set_setting(db, "hero_title", (title or "").strip()[:HERO_TITLE_MAX])
    flash(request, "首页文案已保存。", "success")
    return RedirectResponse("/admin#homecopy", status_code=303)


@app.post("/admin/tracks/{track_id}/rename")
def admin_track_rename(
    track_id: int,
    request: Request,
    title: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    track = db.get(Track, track_id)
    display = (title or "").strip()
    if not track:
        flash(request, "曲目不存在。", "error")
    elif not display:
        flash(request, "曲目标题不能为空。", "error")
    else:
        track.title = display[:200]
        db.commit()
        flash(request, "曲目标题已更新。", "success")
    return RedirectResponse("/admin#tracks", status_code=303)


@app.post("/admin/tracks/{track_id}/category")
def admin_track_set_category(
    track_id: int,
    request: Request,
    category: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    track = db.get(Track, track_id)
    if not track:
        flash(request, "曲目不存在。", "error")
        return RedirectResponse("/admin#tracks", status_code=303)
    track.category = normalize_track_category(category)
    db.commit()
    label = TRACK_CATEGORY_LABELS.get(track.category or "", "未分类")
    flash(request, f"已将「{track.title}」设为{label}。", "success")
    return RedirectResponse("/admin#tracks", status_code=303)


@app.post("/chat")
async def post_chat(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    require_csrf(request, form.get("csrf_token"))
    user = current_user(request, db)
    raw_media = [str(v).strip() for v in form.getlist("media") if str(v).strip()]
    if not user:
        return login_redirect("/chat")
    message = str(form.get("content") or "").strip()
    reply_raw = str(form.get("reply_to") or "").strip()

    def drop_uploads() -> None:
        delete_chat_media_files(db, [n for n in raw_media if ANN_MEDIA_NAME_RE.match(n)])

    reply_to: Optional[ChatMessage] = None
    if reply_raw:
        reply_to = db.get(ChatMessage, int(reply_raw)) if reply_raw.isdigit() else None
        if not reply_to:
            drop_uploads()
            flash(request, "要回复的消息已被删除。", "error")
            return RedirectResponse("/chat", status_code=303)

    items: list[dict] = []
    seen: set[str] = set()
    for name in raw_media:
        if name in seen:
            continue
        if not ANN_MEDIA_NAME_RE.match(name) or not (CHAT_MEDIA_DIR / name).is_file():
            drop_uploads()
            flash(request, "图片/视频已失效，请重新选择。", "error")
            return RedirectResponse("/chat", status_code=303)
        seen.add(name)
        items.append({"file": name, "type": ann_media_type(name)})
    if len(items) > CHAT_MAX_MEDIA:
        drop_uploads()
        flash(request, f"每条消息最多 {CHAT_MAX_MEDIA} 个图片或视频。", "error")
        return RedirectResponse("/chat", status_code=303)
    if items and set(n for n in seen) & referenced_chat_files(db):
        flash(request, "图片/视频已失效，请重新选择。", "error")
        return RedirectResponse("/chat", status_code=303)
    if not message and not items:
        flash(request, "消息不能为空。", "error")
        return RedirectResponse("/chat", status_code=303)
    if len(message) > CHAT_MAX_LEN:
        drop_uploads()
        flash(request, f"消息最多 {CHAT_MAX_LEN} 个字符。", "error")
        return RedirectResponse("/chat", status_code=303)
    if message:
        keywords = db.scalars(select(ChatBlockKeyword)).all()
        matched = find_blocked_keyword(message, keywords)
        if matched:
            drop_uploads()
            db.add(
                ChatBlockLog(
                    user_id=user.id,
                    content=(message + (f" [附 {len(items)} 个媒体，已丢弃]" if items else ""))[:500],
                    keyword=matched[:KEYWORD_MAX_LEN],
                )
            )
            db.commit()
            flash(request, "消息包含敏感词，未能发送", "error")
            return RedirectResponse("/chat", status_code=303)
    db.add(
        ChatMessage(
            user_id=user.id,
            content=message,
            media_json=dump_media(items),
            reply_to_id=reply_to.id if reply_to else None,
            reply_user=display_name(reply_to.user)[:80] if reply_to else None,
            reply_excerpt=chat_excerpt(reply_to)[:120] if reply_to else None,
        )
    )
    db.commit()
    return RedirectResponse("/chat", status_code=303)


@app.post("/chat/{msg_id}/delete")
async def chat_delete_own(msg_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    require_csrf(request, form.get("csrf_token") or request.headers.get("x-csrf-token"))
    user = current_user(request, db)
    as_json = wants_json(request)

    def fail(msg: str, code: int):
        if as_json:
            return JSONResponse({"ok": False, "error": msg}, status_code=code)
        flash(request, msg, "error")
        return RedirectResponse("/chat", status_code=303)

    if not user:
        if as_json:
            return JSONResponse({"ok": False, "error": "请先登录", "login": "/login?next=/chat"}, status_code=401)
        return login_redirect("/chat")
    msg = db.get(ChatMessage, msg_id)
    if not msg:
        if as_json:
            return JSONResponse({"ok": True, "gone": True})
        return RedirectResponse("/chat", status_code=303)
    allowed, err = chat_can_delete(user, msg)
    if not allowed:
        return fail(err, 403)
    names = [m["file"] for m in chat_media(msg)]
    db.delete(msg)
    db.commit()
    delete_chat_media_files(db, names)
    if as_json:
        return JSONResponse({"ok": True})
    flash(request, "消息已删除。", "success")
    return RedirectResponse("/chat", status_code=303)


@app.get("/api/chat")
def api_chat(request: Request, db: Session = Depends(get_db)):
    messages = db.scalars(
        select(ChatMessage).order_by(ChatMessage.created_at.desc()).limit(CHAT_LIMIT)
    ).all()
    me = current_user(request, db)
    messages = list(reversed(list(messages)))
    replies = chat_reply_info(db, messages)
    items = []
    for msg in messages:
        items.append(
            {
                "id": msg.id,
                "mine": bool(me and msg.user_id == me.id),
                "excerpt": chat_excerpt(msg),
                "reply": replies.get(msg.id),
                "username": display_name(msg.user),
                "content": msg.content,
                "media": [
                    {"type": m["type"], "url": m["url"]} for m in chat_media(msg)
                ],
                "time": fmt_time(msg.created_at),
                "created": chat_created_ms(msg.created_at),
                "can_delete": chat_can_delete(me, msg)[0],
            }
        )
    return JSONResponse({"messages": items})



def _looks_like_ip(value: str) -> bool:
    v = (value or "").strip()
    if not v or v in (".", "?") or len(v) > 64:
        return False
    try:
        import ipaddress
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return False


def client_ip(request: Request) -> str:
    """Client IP as seen by the app behind local nginx + Cloudflare.

    Forwarded headers are trusted only when the peer is localhost (uvicorn
    binds 127.0.0.1 and nginx sits in front). Prefer CF-Connecting-IP;
    fall back to the first X-Forwarded-For hop only if it is a valid IP.
    """
    peer = (request.client.host if request.client else "") or ""
    trusted_peer = peer in ("127.0.0.1", "::1") or peer.startswith("127.")
    if trusted_peer:
        cf = (request.headers.get("cf-connecting-ip") or "").strip()
        if _looks_like_ip(cf):
            return cf[:64]
        xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if _looks_like_ip(xff):
            return xff[:64]
    return (peer or "?").strip()[:64] or "?"


def admin_ip_allowed(request: Request) -> bool:
    """Optional allowlist from ADMIN_IP_ALLOWLIST (comma-separated). Empty = disabled."""
    raw = (os.environ.get("ADMIN_IP_ALLOWLIST") or "").strip()
    if not raw:
        return True
    allowed = {part.strip() for part in raw.split(",") if part.strip()}
    return client_ip(request) in allowed


# Failed admin-login limiter: 5 failures in 15 minutes -> 20 minute lockout, per IP.
ADMIN_LOGIN_FAIL_WINDOW = 15 * 60
ADMIN_LOGIN_FAIL_MAX = 5
ADMIN_LOGIN_LOCK_SECONDS = 20 * 60
_admin_login_lock = threading.Lock()
_admin_login_fails: dict[str, list[float]] = {}
_admin_login_until: dict[str, float] = {}


def admin_login_lock_remaining(ip: str) -> int:
    now = time.time()
    with _admin_login_lock:
        until = _admin_login_until.get(ip) or 0
        if until <= now:
            _admin_login_until.pop(ip, None)
            return 0
        return int(until - now)


def record_admin_login_failure(ip: str) -> int:
    """Record a failed attempt and return remaining lockout seconds (0 if not locked)."""
    now = time.time()
    with _admin_login_lock:
        until = _admin_login_until.get(ip) or 0
        if until > now:
            return int(until - now)
        recent = [ts for ts in _admin_login_fails.get(ip, []) if now - ts <= ADMIN_LOGIN_FAIL_WINDOW]
        recent.append(now)
        _admin_login_fails[ip] = recent
        if len(recent) >= ADMIN_LOGIN_FAIL_MAX:
            _admin_login_until[ip] = now + ADMIN_LOGIN_LOCK_SECONDS
            _admin_login_fails.pop(ip, None)
            return ADMIN_LOGIN_LOCK_SECONDS
        return 0


def clear_admin_login_failures(ip: str) -> None:
    with _admin_login_lock:
        _admin_login_fails.pop(ip, None)
        _admin_login_until.pop(ip, None)


def require_admin(request: Request, db: Session) -> User:
    if not admin_ip_allowed(request):
        raise HTTPException(status_code=403, detail="禁止访问")
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/admin"})
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


# Automatic backup status for the admin 「备份记录」 tab. The job itself runs
# as a root systemd oneshot (nice/ionice) outside the web process.
BAK_AUTO = Path("/opt/dj666_backups/auto")
BAK_CTL = Path("/opt/dj666/scripts/dj666_backup_ctl")


def _bak_fmt_bytes(n: int) -> str:
    n = int(n or 0)
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if abs(n) >= size:
            return f"{n / size:.2f} {unit}"
    return f"{n} B"


def _bak_myt_from_iso(raw: str) -> str:
    try:
        dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ_CN).strftime("%Y-%m-%d %H:%M:%S") + " MYT"
    except (TypeError, ValueError):
        return raw or ""


def _bak_next_run() -> Optional[str]:
    try:
        p = subprocess.run(
            ["systemctl", "show", "dj666-backup.timer", "-p", "NextElapseUSecRealtime", "--value"],
            capture_output=True, text=True, timeout=5,
        )
        raw = (p.stdout or "").strip()
        if not raw or raw in {"n/a", "0", "infinity"}:
            return None
        if raw.isdigit():
            dt = datetime.fromtimestamp(int(raw) / 1_000_000, timezone.utc)
        else:
            m = re.search(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})", raw)
            if not m:
                return None
            dt = datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc if "UTC" in raw else TZ_CN)
        return dt.astimezone(TZ_CN).strftime("%Y-%m-%d %H:%M:%S") + " MYT"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _bak_running() -> bool:
    try:
        p = subprocess.run(
            ["systemctl", "is-active", "dj666-backup.service"],
            capture_output=True, text=True, timeout=5,
        )
        return (p.stdout or "").strip() == "active"
    except (OSError, subprocess.TimeoutExpired):
        return False


def _bak_status_payload() -> dict:
    last = {}
    try:
        raw = json.loads((BAK_AUTO / "status.json").read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            last = raw.get("last") if isinstance(raw.get("last"), dict) else raw
    except (OSError, ValueError):
        last = {}
    recent = []
    # Prefer snapshot folders (one row per backup) then fill from the log.
    snaps = []
    snap_dir = BAK_AUTO / "snaps"
    try:
        names = sorted((p for p in snap_dir.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
    except OSError:
        names = []
    for p in names[:40]:
        meta = {}
        try:
            meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        stats = meta.get("stats") if isinstance(meta.get("stats"), dict) else {}
        try:
            ts = datetime.strptime(p.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            time_myt = ts.astimezone(TZ_CN).strftime("%Y-%m-%d %H:%M:%S") + " MYT"
        except ValueError:
            time_myt = meta.get("created_myt") or p.name
        unique = stats.get("bytes_unique")
        apparent = stats.get("bytes_apparent")
        size = unique if unique is not None else apparent
        recent.append({
            "time_myt": time_myt,
            "result": "ok",
            "seconds": None,
            "files": stats.get("files"),
            "size_human": (_bak_fmt_bytes(size) + "（新增）") if unique is not None else (_bak_fmt_bytes(apparent or 0) if apparent else "—"),
            "path": str(p),
            "error": "",
            "note": f"完整体积 {_bak_fmt_bytes(apparent)}" if apparent and unique is not None else "",
        })
    # Merge timing / result from the jsonl log (latest record per snap path/name)
    log_by_name = {}
    try:
        lines = (BAK_AUTO / "backup.log.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines[-200:]:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        key = rec.get("name") or rec.get("snap") or rec.get("start")
        if key:
            log_by_name[key] = rec
    for item in recent:
        rec = log_by_name.get(Path(item["path"]).name) or log_by_name.get(item["path"])
        if not rec:
            continue
        item["result"] = rec.get("result") or item["result"]
        item["seconds"] = rec.get("seconds")
        item["error"] = rec.get("error") or ""
        if rec.get("seconds") is not None and not item.get("note"):
            item["note"] = ""
    # Also surface skip/fail log rows that have no snap folder
    seen = {Path(i["path"]).name for i in recent}
    extra = []
    for rec in log_by_name.values():
        name = rec.get("name") or ""
        if name in seen or rec.get("result") == "ok":
            continue
        extra.append({
            "time_myt": rec.get("end_myt") or rec.get("start_myt") or _bak_myt_from_iso(rec.get("end") or rec.get("start") or ""),
            "result": rec.get("result") or "fail",
            "seconds": rec.get("seconds"),
            "files": rec.get("files"),
            "size_human": _bak_fmt_bytes(rec.get("bytes_unique") or 0) if rec.get("bytes_unique") is not None else "—",
            "path": rec.get("snap") or "",
            "error": rec.get("error") or "",
            "note": "",
        })
    extra.sort(key=lambda x: x.get("time_myt") or "", reverse=True)
    recent = (recent + extra)[:40]
    recent.sort(key=lambda x: x.get("time_myt") or "", reverse=True)
    try:
        free = shutil.disk_usage("/").free
    except OSError:
        free = 0
    return {
        "path": str(BAK_AUTO),
        "running": _bak_running(),
        "next_myt": _bak_next_run(),
        "disk_free": free,
        "disk_free_human": _bak_fmt_bytes(free),
        "last": last,
        "recent": recent[:30],
    }


@app.get("/admin/backups/status")
def bak_status_ep(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    return _bak_status_payload()


@app.post("/admin/backups/run")
def bak_run_ep(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    require_csrf(request, request.headers.get("x-csrf-token"))
    if _bak_running():
        payload = _bak_status_payload()
        payload["message"] = "备份已在进行中"
        payload["running"] = True
        return payload
    if not BAK_CTL.is_file():
        raise HTTPException(status_code=500, detail="备份程序尚未安装")
    try:
        p = subprocess.run(
            ["sudo", "-n", str(BAK_CTL), "start"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=500, detail=f"无法启动备份（{type(exc).__name__}）")
    if p.returncode != 0:
        raise HTTPException(status_code=500, detail="无法启动备份（权限或服务未安装）")
    payload = _bak_status_payload()
    payload["message"] = "备份已开始，请稍候刷新记录"
    payload["running"] = True
    return payload


def admin_login_form(request: Request, db: Session, error: str = "", account: str = ""):
    """Dedicated backend login UI for /admin. Errors stay on this page, not in a frontend flash."""
    return templates.TemplateResponse(
        request,
        "admin_login.html",
        ctx(request, db, login_error=error, account=account),
    )


@app.post("/admin/login")
def admin_login_submit(
    request: Request,
    account: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    ident = (account or "").strip()
    ip = client_ip(request)
    if not admin_ip_allowed(request):
        logging.warning("admin login blocked by allowlist ip=%s account=%s", ip, ident[:80])
        return admin_login_form(request, db, error="禁止访问。", account=ident[:80])
    locked = admin_login_lock_remaining(ip)
    if locked > 0:
        mins = max(1, (locked + 59) // 60)
        logging.warning("admin login rejected, locked ip=%s remain=%ss account=%s", ip, locked, ident[:80])
        return admin_login_form(
            request, db,
            error=f"尝试次数过多，请在 {mins} 分钟后再试。",
            account=ident[:80],
        )
    user = None
    if "@" in ident:
        user = db.scalar(select(User).where(User.email == ident.lower()))
    elif ident:
        user = db.scalar(select(User).where(func.lower(User.username) == ident.lower()))
    # Wrong password, unknown account, or a non-admin: stay on this form with a short error.
    # Do not flash (would leak onto frontend pages) and do not reveal whether the account is admin.
    if not user or not verify_password(password, user.password_hash) or not user.is_admin:
        remain = record_admin_login_failure(ip)
        logging.warning("admin login failed ip=%s account=%s lock=%ss", ip, ident[:80], remain)
        if remain > 0:
            mins = max(1, (remain + 59) // 60)
            err = f"尝试次数过多，请在 {mins} 分钟后再试。"
        else:
            err = "账号或密码不正确。"
        return admin_login_form(request, db, error=err, account=ident[:80])
    clear_admin_login_failures(ip)
    logging.info("admin login ok ip=%s account=%s", ip, ident[:80])
    establish_session(request, user)
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request, db: Session = Depends(get_db)):
    """GET /admin/login must show the same dedicated form (not HTTP 405).
    Already-logged-in admins go to the dashboard."""
    user = current_user(request, db)
    if user and user.is_admin:
        return RedirectResponse("/admin", status_code=303)
    return admin_login_form(request, db)


def flac_admin_stats(db: Session) -> dict:
    tracks = db.scalars(select(Track)).all()
    count, total, pinned = dj_flac.flac_usage(tracks)
    quota = dj_flac.flac_quota_bytes(lambda k, d="": get_setting(db, k, d))
    raw_log = get_setting(db, "flac_prune_log", "[]") or "[]"
    try:
        prune_log = json.loads(raw_log)
        if not isinstance(prune_log, list):
            prune_log = []
    except json.JSONDecodeError:
        prune_log = []
    try:
        gb = float(get_setting(db, "flac_quota_gb", "1000") or "1000")
    except ValueError:
        gb = 1000.0
    return {
        "count": count,
        "total": total,
        "pinned": pinned,
        "quota": quota,
        "quota_gb": gb,
        "prune_log": list(reversed(prune_log[-12:])),
    }


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or not user.is_admin:
        return admin_login_form(request, db)
    try:
        ann_page = int(request.query_params.get("apage") or 1)
    except ValueError:
        ann_page = 1
    announcements, ann_total, ann_page, ann_pages = paginate_announcements(
        db, page=ann_page, page_size=ADMIN_ANN_PAGE_SIZE, include_hidden=True
    )
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    tracks = (
        db.scalars(
            select(Track)
            .options(joinedload(Track.uploader))
            .order_by(Track.created_at.desc())
        )
        .unique()
        .all()
    )
    pending_tracks = [item for item in tracks if item.status == TRACK_STATUS_PENDING]
    published_tracks = [item for item in tracks if item.status == TRACK_STATUS_APPROVED]
    rejected_tracks = [item for item in tracks if item.status == TRACK_STATUS_REJECTED]
    chat_messages = db.scalars(
        select(ChatMessage)
        .options(joinedload(ChatMessage.user))
        .order_by(ChatMessage.created_at.desc())
    ).unique().all()
    chat_count = len(chat_messages)
    block_keywords = db.scalars(
        select(ChatBlockKeyword).order_by(ChatBlockKeyword.created_at.desc())
    ).all()
    tg_new_total = db.scalar(
        select(func.count()).select_from(TgPost).where(TgPost.status == TG_STATUS_NEW)
    ) or 0
    tg_new_limit = 80
    tg_new = db.scalars(
        select(TgPost).where(TgPost.status == TG_STATUS_NEW).order_by(TgPost.post_id.desc()).limit(tg_new_limit)
    ).all()
    tg_done = db.scalars(
        select(TgPost).where(TgPost.status == TG_STATUS_PUBLISHED).order_by(TgPost.post_id.desc()).limit(10)
    ).all()
    tg_ignored = db.scalars(
        select(TgPost).where(TgPost.status == TG_STATUS_IGNORED).order_by(TgPost.post_id.desc()).limit(40)
    ).all()
    tgf_rules = db.scalars(select(TgFilterRule).order_by(TgFilterRule.id.desc())).all()
    ann_stats = ann_interactions(db, [a.id for a in announcements], None, announcements)
    ann_comments = db.scalars(
        select(AnnouncementComment)
        .options(joinedload(AnnouncementComment.user), joinedload(AnnouncementComment.announcement))
        .order_by(AnnouncementComment.created_at.desc(), AnnouncementComment.id.desc())
        .limit(500)
    ).unique().all()
    ann_comment_total = db.scalar(select(func.count()).select_from(AnnouncementComment)) or 0
    ann_like_total = db.scalar(select(func.count()).select_from(AnnouncementLike)) or 0
    block_logs = db.scalars(
        select(ChatBlockLog)
        .options(joinedload(ChatBlockLog.user))
        .order_by(ChatBlockLog.created_at.desc())
        .limit(BLOCK_LOG_LIMIT)
    ).unique().all()
    return templates.TemplateResponse(
        request,
        "admin.html",
        ctx(
            request,
            db,
            **dj_mixer_ctx(db, current_user(request, db)),
            announcements=announcements,
            ann_total=ann_total,
            ann_page=ann_page,
            ann_pages=ann_pages,
            ann_page_size=ADMIN_ANN_PAGE_SIZE,
            users=users,
            tracks=tracks,
            pending_tracks=pending_tracks,
            published_tracks=published_tracks,
            rejected_tracks=rejected_tracks,
            contact=get_submission_contact(db),
            hero_copy=hero_copy_stored(db),
            hero_copy_defaults={"eyebrow": HERO_EYEBROW_DEFAULT, "title": HERO_TITLE_DEFAULT},
            hero_eyebrow_max=HERO_EYEBROW_MAX,
            hero_title_max=HERO_TITLE_MAX,
            bot=bot_config(db),
            bot_ads=bot_ads(db),
            bot_buttons=bot_buttons(db),
            bot_btn_max=BOT_BTN_MAX,
            chat_messages=chat_messages,
            chat_count=chat_count,
            block_keywords=block_keywords,
            block_logs=block_logs,
            ann_stats=ann_stats,
            ann_comments=ann_comments,
            ann_comment_total=ann_comment_total,
            ann_max_links=ANN_MAX_LINKS,
            ann_like_total=ann_like_total,
            ann_data=[
                {
                    "id": a.id,
                    "title": a.title or "",
                    "content": a.content or "",
                    "media": [
                        {"file": m["file"], "type": m["type"], "url": m["url"], "poster": m.get("poster")}
                        for m in ann_media(a)
                    ],
                    "links": [{"text": l["text"], "url": l["url"]} for l in ann_links(a)],
                }
                for a in announcements
            ],
            tg_new=tg_new,
            tg_new_total=tg_new_total,
            tg_new_limit=tg_new_limit,
            tg_data={
                str(t.id): {
                    "text": t.text or "",
                    "title": t.edit_title or "",
                    "links": [{"text": l["text"], "url": l["url"]} for l in ann_links(post_links_obj(t))],
                    "media": [
                        {"file": m["file"], "type": m["type"], "url": m["url"], "poster": m.get("poster")}
                        for m in parse_media_json(t.media_json)
                    ],
                }
                for t in tg_new
            },
            tg_done=tg_done,
            tg_ignored=tg_ignored,
            hidden_anns=db.scalars(
                select(Announcement).where(Announcement.hidden == True).order_by(  # noqa: E712
                    Announcement.hidden_at.desc(), Announcement.id.desc()
                )
            ).all(),
            flac_stats=flac_admin_stats(db),
            r2_status=r2_status_snapshot(),
            r2_local_count=sum(1 for tr in tracks if (getattr(tr, "storage", "local") or "local") != "r2"),
            r2_remote_count=sum(1 for tr in tracks if (getattr(tr, "storage", "local") or "local") == "r2"),
            tgf_strip=[r for r in tgf_rules if r.mode == "strip"],
            tgf_block=[r for r in tgf_rules if r.mode == "block"],
            tgf_gamble=[r for r in tgf_rules if r.mode == "gamble"],
            tgf_images=[r for r in tgf_rules if r.mode == "image"],
            tgf_cfg=tg_filter_settings(db),
            tgf_levels=TG_FILTER_THRESHOLD_LABELS,
            tgf_pil=PILImage is not None,
            tg_cfg=tg_settings(db),
            tg_channel=TG_CHANNEL,
            tg_running=_tg_state.get("running", False),
            tg_intervals=TG_INTERVAL_CHOICES,
            tg_auto_publish_delays=TG_AUTO_PUBLISH_DELAY_CHOICES,
            ann_max_media=ANN_MAX_MEDIA,
            ann_max_mb=ANN_MEDIA_MAX_BYTES // (1024 * 1024),
        ),
    )


def collect_media_from_form(form_values: list[str]) -> tuple[list[dict], Optional[str]]:
    items: list[dict] = []
    seen: set[str] = set()
    for raw in form_values:
        name = (raw or "").strip()
        if not name or name in seen:
            continue
        if not ANN_MEDIA_NAME_RE.match(name):
            return [], "媒体文件无效，请重新上传。"
        if not (ANN_MEDIA_DIR / name).is_file():
            return [], "有媒体文件已失效，请移除后重新上传。"
        seen.add(name)
        items.append({"file": name, "type": ann_media_type(name)})
    if len(items) > ANN_MAX_MEDIA:
        return [], f"每条公告最多 {ANN_MAX_MEDIA} 个图片或视频。"
    return items, None


def attach_posters(items: list[dict], previous: list[dict]) -> list[dict]:
    posters = {p["file"]: p.get("poster_file") for p in previous if p.get("poster_file")}
    for item in items:
        if item["file"] in posters:
            item["poster_file"] = posters[item["file"]]
    return items


def media_from_request_form(form) -> list[str]:
    return [str(v) for v in form.getlist("media")]


async def stream_media_upload(request: Request, folder: Path, url_prefix: str) -> JSONResponse:
    """Stream one image/video (raw request body) into *folder* and return JSON."""
    try:
        require_csrf(request, request.headers.get("x-csrf-token"))
    except HTTPException:
        return JSONResponse({"ok": False, "error": "页面已过期，请刷新后重试"}, status_code=400)
    from urllib.parse import unquote

    original = Path(unquote(request.headers.get("x-file-name", ""))).name
    ext = Path(original).suffix.lower()
    if ext not in ANN_MEDIA_EXTS:
        return JSONResponse(
            {"ok": False, "error": "仅支持图片 jpg/png/gif/webp 和视频 mp4/webm/mov"},
            status_code=400,
        )
    folder.mkdir(parents=True, exist_ok=True)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit():
        if int(declared) > ANN_MEDIA_MAX_BYTES:
            return JSONResponse({"ok": False, "error": f"单个文件不能超过 {ANN_MEDIA_MAX_BYTES // (1024 * 1024)}MB"}, status_code=413)
        if int(declared) + ANN_DISK_RESERVE_BYTES > shutil.disk_usage(folder).free:
            return JSONResponse({"ok": False, "error": "服务器磁盘空间不足"}, status_code=507)
    if ext == ".jpeg":
        ext = ".jpg"
    name = f"{uuid.uuid4().hex}{ext}"
    tmp = folder / f".up-{name}.part"
    size = 0
    try:
        with tmp.open("wb") as out:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > ANN_MEDIA_MAX_BYTES:
                    raise ValueError("too large")
                out.write(chunk)
    except ValueError:
        tmp.unlink(missing_ok=True)
        return JSONResponse({"ok": False, "error": "文件过大"}, status_code=413)
    except Exception:
        tmp.unlink(missing_ok=True)
        return JSONResponse({"ok": False, "error": "上传中断，请重试"}, status_code=400)
    if size == 0 or not sniff_media_ok(tmp, ext):
        tmp.unlink(missing_ok=True)
        return JSONResponse({"ok": False, "error": "文件内容与格式不符或为空"}, status_code=400)
    dest = folder / name
    tmp.rename(dest)
    dest.chmod(0o644)
    return JSONResponse(
        {"ok": True, "file": name, "type": ann_media_type(name), "url": f"{url_prefix}{name}", "size": size}
    )


@app.post("/admin/announcements/media")
async def admin_ann_media_upload(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or not user.is_admin:
        return JSONResponse({"ok": False, "error": "需要管理员权限"}, status_code=403)
    db.close()
    return await stream_media_upload(request, ANN_MEDIA_DIR, "/media/ann/")


@app.post("/chat/media")
async def chat_media_upload(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "error": "请先登录"}, status_code=403)
    db.close()
    return await stream_media_upload(request, CHAT_MEDIA_DIR, "/media/chat/")


@app.get("/media/chat/{filename}")
def serve_chat_media(filename: str):
    if not ANN_MEDIA_NAME_RE.match(filename or ""):
        raise HTTPException(status_code=404, detail="文件不存在")
    path = CHAT_MEDIA_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(
        path,
        media_type=ANN_MEDIA_MIME.get(path.suffix.lower(), "application/octet-stream"),
        headers={"Cache-Control": "public, max-age=604800", "X-Content-Type-Options": "nosniff"},
    )


@app.post("/admin/announcements/create")
async def admin_ann_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    require_csrf(request, form.get("csrf_token"))
    require_admin(request, db)
    title = str(form.get("title") or "").strip()
    content = str(form.get("content") or "").strip()
    items, err = collect_media_from_form(media_from_request_form(form))
    links, lerr = parse_ann_links_form(form)
    err = err or lerr
    if err:
        flash(request, err, "error")
        return RedirectResponse("/admin#announce", status_code=303)
    if not content and not items and not title and not links:
        flash(request, "请填写内容或添加图片/视频。", "error")
        return RedirectResponse("/admin#announce", status_code=303)
    tg_media: list[dict] = []
    for raw in db.scalars(select(TgPost.media_json).where(TgPost.media_json.is_not(None))).all():
        tg_media += parse_media_json(raw)
    items = attach_posters(items, tg_media)
    ann = Announcement(title=title[:200], content=content, media_json=dump_media(items), links_json=dump_links(links))
    db.add(ann)
    db.flush()
    tg_id = str(form.get("tg_id") or "")
    if tg_id.isdigit():
        post = db.get(TgPost, int(tg_id))
        if post:
            post.status = TG_STATUS_PUBLISHED
            post.announcement_id = ann.id
            if post.content_edited or content != (post.text or "").strip():
                post.content_edited = True
                ann.content_edited = True
    db.commit()
    extra = []
    if items:
        extra.append(f"{len(items)} 个媒体")
    if links:
        extra.append(f"{len(links)} 行文案链接")
    flash(request, f"公告已发布（含 {'、'.join(extra)}）。" if extra else "公告已发布。", "success")
    return RedirectResponse("/admin#announce", status_code=303)


@app.post("/admin/announcements/{ann_id}/edit")
async def admin_ann_edit(ann_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    require_csrf(request, form.get("csrf_token"))
    require_admin(request, db)
    ann = db.get(Announcement, ann_id)
    if not ann:
        flash(request, "公告不存在。", "error")
        return RedirectResponse("/admin#announce", status_code=303)
    title = str(form.get("title") or "").strip()
    content = str(form.get("content") or "").strip()
    has_media_field = form.get("media_present") == "1"
    previous = ann_media(ann)
    if has_media_field:
        items, err = collect_media_from_form(media_from_request_form(form))
        if err:
            flash(request, err, "error")
            return RedirectResponse("/admin#announce", status_code=303)
        items = attach_posters(items, previous)
    else:
        items = previous
    if form.get("links_present") == "1":
        links, lerr = parse_ann_links_form(form)
        if lerr:
            flash(request, lerr, "error")
            return RedirectResponse("/admin#announce", status_code=303)
    else:
        links = [{"text": l["text"], "url": l["url"]} for l in ann_links(ann)]
    if not content and not items and not title and not links:
        flash(request, "请填写内容或添加图片/视频。", "error")
        return RedirectResponse("/admin#announce", status_code=303)
    ann.links_json = dump_links(links)
    ann.title = title[:200]
    ann.content = content
    ann.media_json = dump_media(items)
    ann.content_edited = True
    ann.updated_at = datetime.now(timezone.utc)
    db.commit()
    kept = set(media_names(items))
    delete_unreferenced_media(db, [n for n in media_names(previous) if n not in kept])
    flash(request, "公告内容已保存。日后从 Telegram 再次发布同一来源时，将保留这份已编辑内容，不会被原文覆盖。", "success")
    return RedirectResponse("/admin#announce", status_code=303)


@app.post("/admin/announcements/{ann_id}/delete")
def admin_ann_delete(
    ann_id: int,
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    ann = db.get(Announcement, ann_id)
    if ann:
        names = media_names(ann_media(ann))
        for post in db.scalars(select(TgPost).where(TgPost.announcement_id == ann.id)).all():
            post.announcement_id = None
            post.status = TG_STATUS_NEW
        db.query(AnnouncementLike).filter(AnnouncementLike.announcement_id == ann.id).delete()
        db.query(AnnouncementComment).filter(AnnouncementComment.announcement_id == ann.id).delete()
        db.delete(ann)
        db.commit()
        delete_unreferenced_media(db, names)
        flash(request, "公告已删除。", "success")
    return RedirectResponse("/admin#announce", status_code=303)


def wants_json(request: Request) -> bool:
    return request.headers.get("x-requested-with") == "dj666-ajax" or "application/json" in (
        request.headers.get("accept") or ""
    )


def ann_back(ann_id: int) -> RedirectResponse:
    return RedirectResponse(f"/#ann-{ann_id}", status_code=303)


def ann_login_url(ann_id: int) -> str:
    return f"/login?next=/%23ann-{ann_id}"


def ann_fail(request: Request, ann_id: int, message: str, status: int = 400):
    if wants_json(request):
        return JSONResponse({"ok": False, "error": message}, status_code=status)
    flash(request, message, "error")
    return ann_back(ann_id)


def comment_json(c: AnnouncementComment, viewer: Optional[User]) -> dict:
    return {
        "id": c.id,
        "username": display_name(c.user),
        "content": c.content,
        "time": fmt_time(c.created_at),
        "can_delete": bool(viewer and (viewer.is_admin or viewer.id == c.user_id)),
    }


@app.post("/announcements/{ann_id}/like")
def ann_like_toggle(
    ann_id: int,
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = current_user(request, db)
    if not user:
        if wants_json(request):
            return JSONResponse({"ok": False, "login": ann_login_url(ann_id), "error": "请先登录后再点赞"}, status_code=401)
        flash(request, "请先登录后再点赞。", "error")
        return RedirectResponse(ann_login_url(ann_id), status_code=303)
    if not db.get(Announcement, ann_id):
        return ann_fail(request, ann_id, "公告不存在或已删除。", 404)
    existing = db.scalar(
        select(AnnouncementLike).where(
            AnnouncementLike.announcement_id == ann_id, AnnouncementLike.user_id == user.id
        )
    )
    if existing:
        db.delete(existing)
        liked = False
    else:
        db.add(AnnouncementLike(announcement_id=ann_id, user_id=user.id))
        liked = True
    try:
        db.commit()
    except Exception:
        db.rollback()
        liked = True
    likes = db.scalar(
        select(func.count()).select_from(AnnouncementLike).where(AnnouncementLike.announcement_id == ann_id)
    ) or 0
    total = likes + ann_tg_parts(db.get(Announcement, ann_id))["likes"]
    if wants_json(request):
        return JSONResponse({"ok": True, "liked": liked, "likes": total, "likes_text": format_cn_count(total), "real_likes": likes})
    return ann_back(ann_id)


@app.post("/announcements/{ann_id}/comments")
def ann_comment_create(
    ann_id: int,
    request: Request,
    content: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = current_user(request, db)
    if not user:
        if wants_json(request):
            return JSONResponse({"ok": False, "login": ann_login_url(ann_id), "error": "请先登录后再评论"}, status_code=401)
        flash(request, "请先登录后再评论。", "error")
        return RedirectResponse(ann_login_url(ann_id), status_code=303)
    if not db.get(Announcement, ann_id):
        return ann_fail(request, ann_id, "公告不存在或已删除。", 404)
    message = (content or "").replace("\r\n", "\n").strip()
    message = re.sub(r"\n{3,}", "\n\n", message)
    if not message:
        return ann_fail(request, ann_id, "评论不能为空。")
    if len(message) > ANN_COMMENT_MAX_LEN:
        return ann_fail(request, ann_id, f"评论最多 {ANN_COMMENT_MAX_LEN} 个字符。")
    last = db.scalar(
        select(AnnouncementComment.created_at)
        .where(AnnouncementComment.user_id == user.id)
        .order_by(AnnouncementComment.created_at.desc())
        .limit(1)
    )
    if last is not None:
        last_utc = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last_utc).total_seconds() < ANN_COMMENT_COOLDOWN:
            return ann_fail(request, ann_id, "评论太快了，请稍后再试。", 429)
    matched = find_blocked_keyword(message, db.scalars(select(ChatBlockKeyword)).all())
    if matched:
        db.add(
            ChatBlockLog(
                user_id=user.id,
                content=f"[公告评论 #{ann_id}] {message}"[:500],
                keyword=matched[:KEYWORD_MAX_LEN],
            )
        )
        db.commit()
        return ann_fail(request, ann_id, "评论包含敏感词，未能发送。")
    comment = AnnouncementComment(announcement_id=ann_id, user_id=user.id, content=message)
    db.add(comment)
    db.commit()
    db.refresh(comment)
    count = db.scalar(
        select(func.count()).select_from(AnnouncementComment).where(AnnouncementComment.announcement_id == ann_id)
    ) or 0
    total = count + ann_tg_parts(db.get(Announcement, ann_id))["comments"]
    if wants_json(request):
        return JSONResponse({"ok": True, "comment": comment_json(comment, user), "comments": total,
                             "comments_text": format_cn_count(total), "real_comments": count})
    return ann_back(ann_id)


@app.post("/announcements/comments/{comment_id}/delete")
def ann_comment_delete_own(
    comment_id: int,
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = current_user(request, db)
    comment = db.get(AnnouncementComment, comment_id)
    ann_id = comment.announcement_id if comment else 0
    if not user:
        return ann_fail(request, ann_id, "请先登录。", 401)
    if not comment:
        return ann_fail(request, ann_id, "评论不存在或已删除。", 404)
    if not (user.is_admin or comment.user_id == user.id):
        return ann_fail(request, ann_id, "只能删除自己的评论。", 403)
    db.delete(comment)
    db.commit()
    count = db.scalar(
        select(func.count()).select_from(AnnouncementComment).where(AnnouncementComment.announcement_id == ann_id)
    ) or 0
    total = count + ann_tg_parts(db.get(Announcement, ann_id))["comments"]
    if wants_json(request):
        return JSONResponse({"ok": True, "comments": total, "comments_text": format_cn_count(total), "real_comments": count})
    return ann_back(ann_id)


@app.post("/admin/ann-comments/{comment_id}/delete")
def admin_ann_comment_delete(
    comment_id: int,
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    comment = db.get(AnnouncementComment, comment_id)
    if comment:
        db.delete(comment)
        db.commit()
        flash(request, "评论已删除。", "success")
    else:
        flash(request, "评论不存在或已删除。", "error")
    return RedirectResponse("/admin#anncomments", status_code=303)


@app.post("/admin/ann-comments/batch-delete")
async def admin_ann_comment_batch_delete(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    require_admin(request, db)
    ids = []
    for v in form.getlist("ids"):
        try:
            ids.append(int(str(v)))
        except ValueError:
            continue
    if not ids:
        flash(request, "请先勾选要删除的评论。", "error")
        return RedirectResponse("/admin#anncomments", status_code=303)
    n = db.query(AnnouncementComment).filter(AnnouncementComment.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    flash(request, f"已删除 {n} 条评论。", "success")
    return RedirectResponse("/admin#anncomments", status_code=303)


@app.post("/admin/telegram/fetch")
async def admin_tg_fetch(request: Request, csrf_token: str = Form(""), db: Session = Depends(get_db)):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    if _tg_state.get("running"):
        flash(request, "已有采集任务正在进行，请稍后刷新本页查看进度。", "info")
    else:
        _tg_state["running"] = True
        set_setting(db, "tg_last_result", "采集中…正在拉取近一个月内容")
        threading.Thread(target=collect_telegram, args=("manual",), name="tg-collect-manual", daemon=True).start()
        flash(
            request,
            "已开始采集近一个月内容，将按现有「过滤内容」规则自动处理，结果只进入待处理列表，不会自动发布。请稍后刷新本页查看进度；完成后可在确认后点「一键发布」。",
            "success",
        )
    return RedirectResponse("/admin#telegram", status_code=303)


@app.get("/admin/telegram/status")
def admin_tg_status(request: Request, db: Session = Depends(get_db)):
    """Lightweight poll for collect progress. Does not return post bodies or media."""
    require_admin(request, db)
    cfg = tg_settings(db)
    new_total = db.scalar(
        select(func.count()).select_from(TgPost).where(TgPost.status == TG_STATUS_NEW)
    ) or 0
    return JSONResponse(
        {
            "ok": True,
            "running": bool(_tg_state.get("running")),
            "last_run": cfg["last_run"] or "",
            "last_result": cfg["last_result"] or "",
            "new_total": int(new_total),
            "auto_publish": bool(cfg.get("auto_publish")),
            "auto_publish_delay": cfg.get("auto_publish_delay") or TG_AUTO_PUBLISH_DELAY_DEFAULT,
            "last_auto_publish": cfg.get("last_auto_publish") or "",
            "last_eng_sync": cfg.get("last_eng_sync") or "",
            "cmt_status": tgc_status_text(db),
        }
    )


@app.post("/admin/telegram/settings")
def admin_tg_settings(
    request: Request,
    csrf_token: str = Form(""),
    enabled: str = Form(""),
    interval: str = Form(""),
    auto_publish: str = Form(""),
    auto_publish_delay: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    try:
        minutes = int(interval)
    except ValueError:
        minutes = TG_DEFAULT_INTERVAL_MIN
    if minutes not in TG_INTERVAL_CHOICES:
        minutes = TG_DEFAULT_INTERVAL_MIN
    try:
        delay = int(auto_publish_delay)
    except ValueError:
        delay = TG_AUTO_PUBLISH_DELAY_DEFAULT
    if delay not in TG_AUTO_PUBLISH_DELAY_CHOICES:
        delay = TG_AUTO_PUBLISH_DELAY_DEFAULT
    set_setting(db, "tg_auto_enabled", "1" if enabled == "1" else "0")
    set_setting(db, "tg_interval_min", str(minutes))
    set_setting(db, "tg_auto_publish", "1" if auto_publish == "1" else "0")
    set_setting(db, "tg_auto_publish_delay", str(delay))
    parts = []
    parts.append(f"定时采集已开启，每 {minutes} 分钟一次。" if enabled == "1" else "定时采集已关闭。")
    if auto_publish == "1":
        parts.append(f"自动发布已开启，内容发布约 {delay} 分钟后自动发到公告（每轮最多 {TG_AUTO_PUBLISH_CAP} 条）。")
    else:
        parts.append("自动发布已关闭，仍可用「一键发布」。")
    flash(request, " ".join(parts), "success")
    return RedirectResponse("/admin#telegram", status_code=303)


def tg_post_to_announcement(post: TgPost, include_source: bool) -> Announcement:
    content = (post.text or "").strip()
    if include_source:
        content = (content + "\n\n" if content else "") + f"来源：{post.url}"
    return Announcement(
        title=(post.edit_title or "").strip()[:200],
        content=content,
        media_json=post.media_json,
        source_url=post.url,
        links_json=dump_links([{"text": l["text"], "url": l["url"]} for l in ann_links(post_links_obj(post))]),
        tg_reactions_json=post.tg_reactions_json,
        tg_views=post.tg_views,
        tg_comment_count=post.tg_comment_count,
        tg_synced_at=post.tg_synced_at,
    )


class _LinksHolder:
    def __init__(self, raw):
        self.links_json = raw


def post_links_obj(post: TgPost) -> "_LinksHolder":
    return _LinksHolder(getattr(post, "edit_links_json", None))


def publish_one_tg_post(db: Session, post: TgPost, include_source: bool) -> tuple[bool, str]:
    """Publish one pending collected post. Dedup by source_url; never overwrite manually edited content."""
    # Final filter gate: pending posts collected before a rule was added are rechecked here
    # (auto-publish, 一键发布, and single publish all go through this function).
    if post.status == TG_STATUS_NEW and not post.filter_override:
        try:
            action, dropped = tg_filter_post(db, post, tg_rules_ctx(db))
        except Exception:  # noqa: BLE001
            logging.exception("publish-time filter failed on post %s", post.id)
            action, dropped = None, []
        if dropped:
            # Defer file deletion to the caller commit; names may already be unlinked later.
            try:
                delete_unreferenced_media(db, dropped)
            except Exception:  # noqa: BLE001
                logging.exception("filter media cleanup failed")
        if post.status != TG_STATUS_NEW:
            return False, "filtered"
    if post.status == TG_STATUS_PUBLISHED and post.announcement_id:
        existing = db.get(Announcement, post.announcement_id)
        if existing:
            copy_engagement_to_ann(existing, post)
            return False, "kept_edited" if existing.content_edited else "already"
    if post.status != TG_STATUS_NEW:
        return False, "not_pending"
    if post.url:
        existing = db.scalar(select(Announcement).where(Announcement.source_url == post.url).limit(1))
        if existing:
            post.status = TG_STATUS_PUBLISHED
            post.announcement_id = existing.id
            copy_engagement_to_ann(existing, post)
            return False, "kept_edited" if existing.content_edited else "dup"
    ann = tg_post_to_announcement(post, include_source)
    ann.content_edited = bool(post.content_edited)
    db.add(ann)
    db.flush()
    post.status = TG_STATUS_PUBLISHED
    post.announcement_id = ann.id
    return True, "ok"


def publish_pending_tg_posts(db: Session, include_source: bool = False) -> tuple[int, int]:
    """Publish every current pending (new, already filtered) item. Returns (published, skipped)."""
    posts = db.scalars(
        select(TgPost).where(TgPost.status == TG_STATUS_NEW).order_by(TgPost.post_id.asc())
    ).all()
    published = skipped = 0
    for post in posts:
        ok, _reason = publish_one_tg_post(db, post, include_source)
        if ok:
            published += 1
        else:
            skipped += 1
    db.commit()
    return published, skipped


@app.post("/admin/telegram/{item_id}/publish")
def admin_tg_publish(
    item_id: int,
    request: Request,
    csrf_token: str = Form(""),
    with_source: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    post = db.get(TgPost, item_id)
    if not post:
        flash(request, "采集内容不存在。", "error")
        return RedirectResponse("/admin#telegram", status_code=303)
    ok, reason = publish_one_tg_post(db, post, with_source == "1")
    db.commit()
    if ok:
        flash(request, "已一键发布为公告，首页可见。", "success")
    elif reason == "kept_edited":
        flash(request, "该来源已有手动编辑的公告，已保留编辑内容，未用采集原文覆盖。", "info")
    elif reason in ("already", "dup"):
        flash(request, "这条已经发布过了，未重复发布。", "info")
    elif reason == "filtered":
        flash(request, "发布前复查过滤规则：该条已按广告规则忽略，未发布到首页。", "info")
    else:
        flash(request, "当前状态不可发布。", "error")
    return RedirectResponse("/admin#telegram", status_code=303)


@app.post("/admin/telegram/publish-all")
def admin_tg_publish_all(
    request: Request,
    csrf_token: str = Form(""),
    with_source: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    published, skipped = publish_pending_tg_posts(db, with_source == "1")
    if published == 0 and skipped == 0:
        flash(request, "当前没有可发布的待处理内容。", "info")
    else:
        msg = f"一键发布完成：已发布 {published} 条到公告首页"
        if skipped:
            msg += f"，跳过 {skipped} 条（已发布、未处于待处理或发布前被过滤）"
        msg += "。"
        flash(request, msg, "success")
    return RedirectResponse("/admin#telegram", status_code=303)


@app.post("/admin/telegram/{item_id}/edit")
async def admin_tg_edit_text(item_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    require_csrf(request, form.get("csrf_token"))
    require_admin(request, db)
    post = db.get(TgPost, item_id)
    if not post or post.status != TG_STATUS_NEW:
        flash(request, "待处理内容不存在或已发布/忽略。", "error")
        return RedirectResponse("/admin#telegram", status_code=303)
    text = str(form.get("text") if form.get("text") is not None else "")
    # Keep raw_text as the collected original; edited text is the user's final copy.
    if post.raw_text is None:
        post.raw_text = post.text or ""
    media = parse_media_json(post.media_json)
    title = re.sub(r"[\r\n]+", " ", str(form.get("title") or "")).strip()[:200]
    if form.get("links_present") == "1":
        links, lerr = parse_ann_links_form(form)
        if lerr:
            flash(request, lerr, "error")
            return RedirectResponse("/admin#telegram", status_code=303)
    else:
        links = [{"text": l["text"], "url": l["url"]} for l in ann_links(post_links_obj(post))]
    if not text.strip() and not media and not title and not links:
        flash(request, "请填写文字，或保留媒体后再保存。", "error")
        return RedirectResponse("/admin#telegram", status_code=303)
    post.text = text
    post.edit_title = title or None
    post.edit_links_json = dump_links(links)
    post.content_edited = True
    db.commit()
    flash(request, "已保存编辑（标题、正文、文案链接）。自动发布或一键发布将使用这份内容，不会被再次采集或过滤规则覆盖。", "success")
    return RedirectResponse("/admin#telegram", status_code=303)


@app.post("/admin/telegram/{item_id}/ignore")
def admin_tg_ignore(
    item_id: int,
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    post = db.get(TgPost, item_id)
    if post and post.status != TG_STATUS_PUBLISHED:
        names = media_names(parse_media_json(post.media_json))
        post.status = TG_STATUS_IGNORED
        post.media_json = None
        db.commit()
        delete_unreferenced_media(db, names)
        flash(request, "已忽略该条（不会再次采集）。", "success")
    return RedirectResponse("/admin#telegram", status_code=303)


@app.post("/admin/telegram/clear")
def admin_tg_clear(
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    names: list[str] = []
    count = 0
    for post in db.scalars(select(TgPost).where(TgPost.status == TG_STATUS_NEW)).all():
        names += media_names(parse_media_json(post.media_json))
        post.status = TG_STATUS_IGNORED
        post.media_json = None
        count += 1
    db.commit()
    delete_unreferenced_media(db, names)
    flash(request, f"已清空 {count} 条待处理采集内容。", "success")
    return RedirectResponse("/admin#telegram", status_code=303)


# --- 过滤内容 (Telegram collection filters) ---------------------------------


def _tgf_redirect(anchor: str = "tgfilter") -> RedirectResponse:
    return RedirectResponse(f"/admin#{anchor}", status_code=303)


def _tgf_rescan_msg(db: Session) -> str:
    ignored, changed = apply_tg_filters(db)
    return f"已重新扫描待处理内容：自动忽略 {ignored} 条，清理 {changed} 条。"


@app.post("/admin/telegram/filters/text")
def admin_tgf_add_text(
    request: Request,
    csrf_token: str = Form(""),
    patterns: str = Form(""),
    mode: str = Form("strip"),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    if mode not in ("strip", "block", "gamble"):
        mode = "strip"
    taken = {
        tg_norm(r.pattern)
        for r in db.scalars(select(TgFilterRule).where(TgFilterRule.mode == mode)).all()
    }
    added = skipped = invalid = 0
    for line in (patterns or "").replace("\r", "\n").split("\n"):
        word = line.strip()
        if not word:
            continue
        norm = tg_norm(word)
        if not norm or len(word) > TG_FILTER_PATTERN_MAX:
            invalid += 1
            continue
        if norm in taken:
            skipped += 1
            continue
        weight = 2
        if mode == "gamble":
            weight = next((w for p, w in DEFAULT_TG_GAMBLE_RULES if tg_norm(p) == norm), 2)
        db.add(TgFilterRule(
            mode=mode, pattern=word, label="赌博广告" if mode == "gamble" else "",
            source="manual", weight=weight,
        ))
        taken.add(norm)
        added += 1
    db.commit()
    if not added:
        msg = "没有添加新规则"
        if skipped:
            msg += f"（{skipped} 条已存在）"
        if invalid:
            msg += f"（{invalid} 条无效：需包含文字/数字，且不超过 {TG_FILTER_PATTERN_MAX} 字）"
        flash(request, msg + "。", "error")
        return _tgf_redirect()
    kind = {"block": "整条忽略", "gamble": "赌博广告"}.get(mode, "去除推广语")
    msg = f"已添加 {added} 条{kind}规则"
    if skipped:
        msg += f"，跳过重复 {skipped} 条"
    if invalid:
        msg += f"，跳过无效 {invalid} 条"
    flash(request, msg + "。" + _tgf_rescan_msg(db), "success")
    return _tgf_redirect()


@app.post("/admin/telegram/filters/images")
async def admin_tgf_add_images(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    require_admin(request, db)
    if PILImage is None:
        flash(request, "服务器缺少图片处理组件（Pillow），暂时无法添加图片规则。", "error")
        return _tgf_redirect()
    label = str(form.get("label") or "").strip()[:100]
    files = [f for f in form.getlist("files") if hasattr(f, "filename") and f.filename]
    if not files:
        flash(request, "请选择至少一张样本图片。", "error")
        return _tgf_redirect()
    ctx_data = tg_rules_ctx(db)
    added = dup = bad = plain = 0
    for up in files[:50]:
        tmp = Path(tempfile.mkstemp(prefix="tgf-", suffix=".img")[1])
        try:
            size = 0
            with tmp.open("wb") as out:
                while True:
                    chunk = await up.read(1024 * 256)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > TG_FILTER_SAMPLE_MAX_BYTES:
                        raise ValueError("too large")
                    out.write(chunk)
            value = tg_image_hash_file(tmp)
            if not value:
                bad += 1
                continue
            res = tg_add_image_rule(db, ctx_data, tmp, value, label or (up.filename or "")[:100], "manual")
            if res == "added":
                added += 1
            elif res == "dup":
                dup += 1
            else:
                plain += 1
        except ValueError:
            bad += 1
        finally:
            tmp.unlink(missing_ok=True)
            await up.close()
    db.commit()
    parts = [f"已添加 {added} 条图片规则"]
    if dup:
        parts.append(f"{dup} 张与已有规则相同")
    if plain:
        parts.append(f"{plain} 张画面过于单一（纯色等）未添加")
    if bad:
        parts.append(f"{bad} 张无法识别或超过 30MB")
    msg = "，".join(parts) + "。"
    if added:
        msg += _tgf_rescan_msg(db)
    flash(request, msg, "success" if added else "error")
    return _tgf_redirect()


@app.post("/admin/telegram/filters/delete")
async def admin_tgf_delete(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    require_admin(request, db)
    ids = [int(v) for v in form.getlist("ids") if str(v).isdigit()]
    count = 0
    for rid in ids:
        rule = db.get(TgFilterRule, rid)
        if not rule:
            continue
        if rule.sample and TG_FILTER_NAME_RE.match(rule.sample):
            (TG_FILTER_DIR / rule.sample).unlink(missing_ok=True)
        db.delete(rule)
        count += 1
    db.commit()
    if not count:
        flash(request, "请先勾选要删除的规则。", "error")
        return _tgf_redirect()
    flash(request, f"已删除 {count} 条过滤规则。" + _tgf_rescan_msg(db), "success")
    return _tgf_redirect()


@app.post("/admin/telegram/filters/settings")
def admin_tgf_settings(
    request: Request,
    csrf_token: str = Form(""),
    autolearn: str = Form(""),
    gamble: str = Form(""),
    level: str = Form("normal"),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    if level not in TG_FILTER_THRESHOLDS:
        level = "normal"
    set_setting(db, "tg_filter_autolearn", "1" if autolearn == "1" else "0")
    set_setting(db, "tg_filter_gamble", "1" if gamble == "1" else "0")
    set_setting(db, "tg_filter_level", level)
    flash(request, "过滤设置已保存。" + _tgf_rescan_msg(db), "success")
    return _tgf_redirect()


@app.post("/admin/telegram/filters/rescan")
def admin_tgf_rescan(request: Request, csrf_token: str = Form(""), db: Session = Depends(get_db)):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    flash(request, _tgf_rescan_msg(db), "success")
    return _tgf_redirect()


@app.get("/admin/telegram/filters/img/{filename}")
def admin_tgf_sample(filename: str, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or not user.is_admin or not TG_FILTER_NAME_RE.match(filename or ""):
        raise HTTPException(status_code=404, detail="文件不存在")
    path = TG_FILTER_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path, media_type="image/jpeg")


@app.post("/admin/telegram/batch")
async def admin_tg_batch(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    require_admin(request, db)
    action = str(form.get("action") or "")
    ids = list(dict.fromkeys(int(v) for v in form.getlist("ids") if str(v).isdigit()))
    anchor = "telegram"
    if not ids:
        flash(request, "请先勾选要操作的条目。", "error")
        return _tgf_redirect(anchor)
    posts = [p for p in (db.get(TgPost, i) for i in ids) if p]
    names: list[str] = []
    count = 0
    if action in ("ignore", "learn"):
        learned = 0
        ctx_data = tg_rules_ctx(db) if action == "learn" else None
        for post in posts:
            if post.status != TG_STATUS_NEW:
                continue
            media = parse_media_json(post.media_json)
            if action == "learn":
                learned += tg_learn_from_media(db, ctx_data, media, f"来自采集 #{post.post_id}", "item")
            names += media_names(media)
            post.status = TG_STATUS_IGNORED
            post.media_json = None
            post.filter_info = "手动忽略（图片已加入过滤）" if action == "learn" else "手动忽略"
            count += 1
        db.commit()
        delete_unreferenced_media(db, names)
        msg = f"已忽略 {count} 条。"
        if action == "learn":
            msg = f"已把 {learned} 张图片加入图片过滤规则，并忽略 {count} 条。" + _tgf_rescan_msg(db)
        flash(request, msg, "success")
    elif action == "publish":
        with_source = str(form.get("with_source") or "") == "1"
        skipped = 0
        kept = 0
        for post in sorted(posts, key=lambda p: p.post_id):
            ok, reason = publish_one_tg_post(db, post, with_source)
            if ok:
                count += 1
            elif reason == "kept_edited":
                kept += 1
            else:
                skipped += 1
        db.commit()
        msg = f"已发布 {count} 条为公告，首页可见。"
        if kept:
            msg += f" 另有 {kept} 条已有手动编辑内容，已保留未覆盖。"
        if skipped:
            msg += f" 跳过 {skipped} 条。"
        flash(request, msg, "success")
    elif action == "restore":
        ctx_data = tg_rules_ctx(db)
        for post in posts:
            if post.status != TG_STATUS_IGNORED:
                continue
            post.status = TG_STATUS_NEW
            post.filter_override = True
            post.raw_text = post.raw_text if post.raw_text is not None else (post.text or "")
            post.text = post.raw_text
            post.filter_info = None
            if not post.media_json and "原媒体已删除" not in (post.note or ""):
                post.note = (((post.note + "；") if post.note else "") + "已从忽略中恢复，原媒体已删除")[:300]
            tg_filter_post(db, post, ctx_data)
            if post.status == TG_STATUS_NEW:
                count += 1
        db.commit()
        msg = f"已恢复 {count} 条到待处理（恢复的条目不再整条自动忽略，推广语仍会去除）。"
        if count < len(posts):
            msg += f"另有 {len(posts) - count} 条去除推广语后已无内容，仍保持忽略。"
        flash(request, msg, "success")
    else:
        flash(request, "未知操作。", "error")
    return _tgf_redirect(anchor)


@app.get("/media/ann/{filename}")
def serve_ann_media(filename: str):
    if not ANN_MEDIA_NAME_RE.match(filename or ""):
        raise HTTPException(status_code=404, detail="文件不存在")
    path = ANN_MEDIA_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    ext = path.suffix.lower()
    return FileResponse(
        path,
        media_type=ANN_MEDIA_MIME.get(ext, "application/octet-stream"),
        headers={"Cache-Control": "public, max-age=604800", "X-Content-Type-Options": "nosniff"},
    )


@app.post("/admin/users/{user_id}/delete")
def admin_user_delete(
    user_id: int,
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    admin = require_admin(request, db)
    target = db.get(User, user_id)
    if not target:
        flash(request, "用户不存在。", "error")
        return RedirectResponse("/admin#users", status_code=303)
    if target.id == admin.id:
        flash(request, "不能删除当前登录的管理员账号。", "error")
        return RedirectResponse("/admin#users", status_code=303)
    db.query(ChatBlockLog).filter(ChatBlockLog.user_id == target.id).update(
        {ChatBlockLog.user_id: None}
    )
    chat_names: list[str] = []
    for raw in db.scalars(
        select(ChatMessage.media_json).where(ChatMessage.user_id == target.id, ChatMessage.media_json.is_not(None))
    ).all():
        chat_names += [m["file"] for m in parse_media_json(raw)]
    db.query(ChatMessage).filter(ChatMessage.user_id == target.id).delete()
    db.query(AnnouncementLike).filter(AnnouncementLike.user_id == target.id).delete()
    db.query(AnnouncementComment).filter(AnnouncementComment.user_id == target.id).delete()
    for track in list(target.tracks):
        dj_flac.drop_track_flac(track, UPLOAD_DIR, FLAC_SNAP_ROOTS)
        delete_track_file(track.filename)
        db.delete(track)
    db.delete(target)
    db.commit()
    delete_chat_media_files(db, chat_names)
    flash(request, "用户及其上传内容已删除。", "success")
    return RedirectResponse("/admin#users", status_code=303)



@app.post("/admin/tracks/{track_id}/approve")
def admin_track_approve(
    track_id: int,
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    track = db.get(Track, track_id)
    if not track:
        flash(request, "曲目不存在。", "error")
        return RedirectResponse("/admin#tracks", status_code=303)
    track.status = TRACK_STATUS_APPROVED
    db.commit()
    flash(request, "已通过审核，曲目将显示在首页音乐列表中。", "success")
    return RedirectResponse("/admin#tracks", status_code=303)


@app.post("/admin/tracks/{track_id}/reject")
def admin_track_reject(
    track_id: int,
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    track = db.get(Track, track_id)
    if not track:
        flash(request, "曲目不存在。", "error")
        return RedirectResponse("/admin#tracks", status_code=303)
    track.status = TRACK_STATUS_REJECTED
    db.commit()
    flash(request, "已拒绝该曲目，它不会出现在首页。", "success")
    return RedirectResponse("/admin#tracks", status_code=303)


@app.post("/admin/tracks/{track_id}/delete")
def admin_track_delete(
    track_id: int,
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    track = db.get(Track, track_id)
    if track:
        dj_flac.drop_track_flac(track, UPLOAD_DIR, FLAC_SNAP_ROOTS, remote_delete=r2_delete_key)
        if getattr(track, "r2_key", None):
            r2_delete_key(track.r2_key)
        delete_track_file(track.filename)
        db.delete(track)
        db.commit()
        flash(request, "曲目已删除。", "success")
    return RedirectResponse("/admin#tracks", status_code=303)



@app.post("/admin/announcements/{ann_id}/hide")
def admin_ann_hide(ann_id: int, request: Request, csrf_token: str = Form(""), reason: str = Form(""), db: Session = Depends(get_db)):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    ann = db.get(Announcement, ann_id)
    if not ann:
        flash(request, "公告不存在。", "error")
        return RedirectResponse("/admin#announce", status_code=303)
    hide_announcement(ann, reason or "管理员隐藏")
    db.commit()
    flash(request, "已从首页隐藏（可在「过滤内容」里恢复）。", "success")
    nxt = request.headers.get("referer") or "/admin#announce"
    if "/admin" not in (nxt or ""):
        nxt = "/admin#announce"
    return RedirectResponse(nxt, status_code=303)


@app.post("/admin/announcements/{ann_id}/unhide")
def admin_ann_unhide(ann_id: int, request: Request, csrf_token: str = Form(""), db: Session = Depends(get_db)):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    ann = db.get(Announcement, ann_id)
    if not ann:
        flash(request, "公告不存在。", "error")
        return RedirectResponse("/admin#tgfilter", status_code=303)
    unhide_announcement(ann)
    db.commit()
    flash(request, "已恢复到首页。", "success")
    return RedirectResponse("/admin#tgfilter", status_code=303)


@app.get("/admin/tracks/{track_id}/flac")
def admin_track_flac_dl(track_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    track = db.get(Track, track_id)
    if not track:
        raise HTTPException(status_code=404, detail="FLAC 不存在")
    stem = (track.title or Path(track.original_name or "track").stem or "track")[:80]
    fname = f"{stem}.flac"
    flac_key = getattr(track, "flac_r2_key", None)
    if flac_key and r2_storage.r2_enabled():
        try:
            url = r2_storage.presigned_url(flac_key, expires=600, filename=fname, content_type="audio/flac")
        except Exception:  # noqa: BLE001
            logging.exception("r2 flac presign failed track=%s", track_id)
            raise HTTPException(status_code=502, detail="FLAC 暂时无法下载")
        return RedirectResponse(url, status_code=303)
    path = dj_flac.resolve_flac(UPLOAD_DIR, getattr(track, "flac_path", None))
    if not path:
        raise HTTPException(status_code=404, detail="FLAC 不存在")
    return FileResponse(
        path, media_type="audio/flac", filename=fname, content_disposition_type="attachment",
    )


@app.post("/admin/tracks/{track_id}/flac/delete")
def admin_track_flac_delete(track_id: int, request: Request, csrf_token: str = Form(""), db: Session = Depends(get_db)):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    track = db.get(Track, track_id)
    if not track or not dj_flac.has_flac(track):
        flash(request, "这首曲目没有可删除的 FLAC。", "error")
        return RedirectResponse("/admin#tracks", status_code=303)
    dj_flac.drop_track_flac(track, UPLOAD_DIR, FLAC_SNAP_ROOTS, remote_delete=r2_delete_key)
    db.commit()
    flash(request, "已删除该曲目的 FLAC（MP3 播放不受影响）。", "success")
    return RedirectResponse("/admin#tracks", status_code=303)


@app.post("/admin/tracks/{track_id}/flac/pin")
def admin_track_flac_pin(track_id: int, request: Request, csrf_token: str = Form(""), pinned: str = Form(""), db: Session = Depends(get_db)):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    track = db.get(Track, track_id)
    if not track or not track.flac_path:
        flash(request, "这首曲目没有 FLAC。", "error")
        return RedirectResponse("/admin#tracks", status_code=303)
    track.flac_pinned = pinned == "1"
    db.commit()
    flash(request, "已永久保留该 FLAC。" if track.flac_pinned else "已取消永久保留。", "success")
    return RedirectResponse("/admin#tracks", status_code=303)


@app.post("/admin/flac/settings")
def admin_flac_settings(request: Request, csrf_token: str = Form(""), quota_gb: str = Form("1000"), db: Session = Depends(get_db)):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    try:
        gb = float((quota_gb or "1000").strip())
    except ValueError:
        gb = 1000.0
    gb = max(0.5, min(gb, 10000.0))
    set_setting(db, "flac_quota_gb", str(gb).rstrip("0").rstrip(".") if gb != int(gb) else str(int(gb)))
    try:
        pruned = dj_flac.enforce_flac_quota(
            db, db.scalars(select(Track)).all(), UPLOAD_DIR, FLAC_SNAP_ROOTS,
            dj_flac.flac_quota_bytes(lambda k, d="": get_setting(db, k, d)),
            OPT_DISK_MIN_FREE,
            lambda k, d="": get_setting(db, k, d),
            lambda k, v: set_setting(db, k, v),
            remote_delete=r2_delete_key,
        )
        db.commit()
    except Exception:  # noqa: BLE001
        logging.exception("flac quota enforce on settings failed")
        db.rollback()
        pruned = []
    msg = f"FLAC 配额已设为 {gb:g} GB。"
    if pruned:
        msg += f" 因超出配额或磁盘空间，已清理 {len(pruned)} 个未保留的旧 FLAC。"
    flash(request, msg, "success")
    return RedirectResponse("/admin#tracks", status_code=303)


@app.post("/admin/password")
def admin_change_password(
    request: Request,
    old_password: str = Form(""),
    new_password: str = Form(""),
    new_password2: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = require_admin(request, db)
    ip = client_ip(request)
    locked = admin_login_lock_remaining(ip)
    if locked > 0:
        mins = max(1, (locked + 59) // 60)
        logging.warning("admin password change rejected, locked ip=%s remain=%ss user_id=%s", ip, locked, user.id)
        flash(request, f"尝试次数过多，请在 {mins} 分钟后再试。", "error")
        return RedirectResponse("/admin#account", status_code=303)
    if not verify_password(old_password, user.password_hash):
        remain = record_admin_login_failure(ip)
        logging.warning(
            "admin password change failed, bad old password ip=%s user_id=%s lock=%ss",
            ip, user.id, remain,
        )
        if remain > 0:
            mins = max(1, (remain + 59) // 60)
            flash(request, f"尝试次数过多，请在 {mins} 分钟后再试。", "error")
        else:
            flash(request, "旧密码不正确。", "error")
        return RedirectResponse("/admin#account", status_code=303)
    if new_password != new_password2:
        flash(request, "两次输入的新密码不一致。", "error")
        return RedirectResponse("/admin#account", status_code=303)
    if len(new_password) < ADMIN_PASSWORD_MIN:
        flash(request, f"新密码至少需要 {ADMIN_PASSWORD_MIN} 位。", "error")
        return RedirectResponse("/admin#account", status_code=303)
    if new_password == old_password or verify_password(new_password, user.password_hash):
        flash(request, "新密码不能与当前密码相同。", "error")
        return RedirectResponse("/admin#account", status_code=303)
    user.password_hash = hash_password(new_password)
    user.session_ver = int(getattr(user, "session_ver", 0) or 0) + 1
    db.commit()
    establish_session(request, user)
    clear_admin_login_failures(ip)
    logging.info("admin password changed user_id=%s ip=%s", user.id, ip)
    flash(request, "密码已更新。其他后台登录已失效，当前会话仍保持登录。", "success")
    return RedirectResponse("/admin#account", status_code=303)


@app.get("/admin/r2/status")
def admin_r2_status(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    snap = r2_status_snapshot()
    tracks = db.scalars(select(Track)).all()
    snap["local_count"] = sum(1 for tr in tracks if (getattr(tr, "storage", "local") or "local") != "r2")
    snap["remote_count"] = sum(1 for tr in tracks if (getattr(tr, "storage", "local") or "local") == "r2")
    return snap


@app.post("/admin/r2/migrate")
def admin_r2_migrate(request: Request, csrf_token: str = Form(""), db: Session = Depends(get_db)):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    if not r2_storage.r2_enabled():
        flash(request, "R2 未启用，无法迁移。", "error")
        return RedirectResponse("/admin#tracks", status_code=303)
    with _R2_LOCK:
        running = _R2_STATUS.get("state") == "running"
    if running:
        flash(request, "迁移已在进行中。", "info")
        return RedirectResponse("/admin#tracks", status_code=303)
    _R2_QUEUE.put(("migrate",))
    flash(request, "已开始把本地曲目迁移到 R2，可在本页查看进度。", "success")
    return RedirectResponse("/admin#tracks", status_code=303)


@app.post("/admin/chat/clear")
def admin_chat_clear(
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    names: list[str] = []
    for raw in db.scalars(select(ChatMessage.media_json).where(ChatMessage.media_json.is_not(None))).all():
        names += [m["file"] for m in parse_media_json(raw)]
    db.query(ChatMessage).delete()
    db.commit()
    delete_chat_media_files(db, names)
    flash(request, "公屏消息已清空（含图片/视频）。", "success")
    return RedirectResponse("/admin#chat", status_code=303)


@app.post("/admin/chat/{msg_id}/delete")
def admin_chat_delete(
    msg_id: int,
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    msg = db.get(ChatMessage, msg_id)
    if not msg:
        flash(request, "消息不存在。", "error")
        return RedirectResponse("/admin#chat", status_code=303)
    names = [m["file"] for m in chat_media(msg)]
    db.delete(msg)
    db.commit()
    delete_chat_media_files(db, names)
    flash(request, "消息已删除。", "success")
    return RedirectResponse("/admin#chat", status_code=303)


@app.post("/admin/chat/keywords/add")
def admin_keyword_add(
    request: Request,
    word: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    word = normalize_keyword(word)
    if not word:
        flash(request, "屏蔽关键词不能为空。", "error")
        return RedirectResponse("/admin#keywords", status_code=303)
    if len(word) > KEYWORD_MAX_LEN:
        flash(request, f"屏蔽关键词最多 {KEYWORD_MAX_LEN} 个字符。", "error")
        return RedirectResponse("/admin#keywords", status_code=303)
    folded = word.casefold()
    existing = db.scalars(select(ChatBlockKeyword)).all()
    if any((item.word or "").strip().casefold() == folded for item in existing):
        flash(request, "该屏蔽关键词已存在。", "error")
        return RedirectResponse("/admin#keywords", status_code=303)
    db.add(ChatBlockKeyword(word=word))
    db.commit()
    flash(request, "屏蔽关键词已添加。", "success")
    return RedirectResponse("/admin#keywords", status_code=303)


@app.post("/admin/chat/keywords/{kw_id}/delete")
def admin_keyword_delete(
    kw_id: int,
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    item = db.get(ChatBlockKeyword, kw_id)
    if not item:
        flash(request, "该屏蔽关键词不存在。", "error")
        return RedirectResponse("/admin#keywords", status_code=303)
    db.delete(item)
    db.commit()
    flash(request, "屏蔽关键词已删除。", "success")
    return RedirectResponse("/admin#keywords", status_code=303)


@app.post("/admin/chat/block-logs/clear")
def admin_block_logs_clear(
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_admin(request, db)
    db.query(ChatBlockLog).delete()
    db.commit()
    flash(request, "拦截记录已清空。", "success")
    return RedirectResponse("/admin#keywords", status_code=303)



# ---------------------------------------------------------------------------
# DJ 上传优化 (server-side loudness / EQ / limiter mastering) + mixer recordings
# ---------------------------------------------------------------------------
import queue as _queue_mod

OPT_DIR = UPLOAD_DIR / "optimize"
OPT_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".webm", ".mp4", ".opus", ".aif", ".aiff"}
OPT_MIME = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac", ".m4a": "audio/mp4",
    ".ogg": "audio/ogg", ".opus": "audio/ogg", ".aac": "audio/aac", ".webm": "audio/webm",
    ".mp4": "audio/mp4", ".aif": "audio/aiff", ".aiff": "audio/aiff",
}
OPT_MAX_BYTES = 1 * 1024 * 1024 * 1024     # non-admin upload cap (6h 320k MP3 ≈ 864MB)
OPT_MAX_BYTES_ADMIN = 2 * 1024 * 1024 * 1024  # admin / Telegram (12h 320k MP3 ≈ 1.65GB)
OPT_CHUNK_BYTES = 8 * 1024 * 1024          # client chunk size (stays far below Cloudflare's body cap)
OPT_CHUNK_MAX = 16 * 1024 * 1024
OPT_MAX_DURATION = 6 * 3600                # non-admin, seconds
OPT_MAX_DURATION_ADMIN = 12 * 3600         # admin
OPT_DISK_MIN_FREE = int(1.5 * 1024 * 1024 * 1024)  # keep at least 1.5GB free after a job's estimated need
OPT_KEEP_SECONDS = 12 * 3600                       # un-imported finished jobs
OPT_FAILED_KEEP_SECONDS = 2 * 3600                 # failed jobs (let the user read the error first)
OPT_UPLOAD_STALE = 6 * 3600                # large chunked uploads may take a while
OPT_USER_ACTIVE_MAX = 2                    # non-admin: unfinished jobs at once
OPT_USER_DAILY_MAX = 10                    # non-admin: jobs per 24h
OPT_QUEUE_MAX = 8                          # non-admin jobs waiting in the global queue
OPT_TARGETS = {"-8": -8.0, "-9": -9.0, "-10": -10.0, "-14": -14.0}
OPT_DEFAULT_TARGET = "-9"
OPT_TP_LIMIT_LINEAR = 0.87                 # ≈ -1.2 dBFS sample ceiling at 4x oversampling -> ≤ -1 dBTP
OPT_MAX_GAIN_DB = 12.0
OPT_EQ = (
    "highpass=f=25:poles=2,"
    "bass=g=1.5:f=90:w=0.7,"
    "equalizer=f=3200:t=q:w=0.9:g=1.0,"
    "treble=g=1.5:f=11000:w=0.6"
)
OPT_ID_RE = re.compile(r"^[a-f0-9]{24}$")
OPT_ACTIVE_STATES = {"uploading", "uploaded", "queued", "running"}


def _opt_max_bytes(is_admin: bool) -> int:
    return OPT_MAX_BYTES_ADMIN if is_admin else OPT_MAX_BYTES


def _opt_max_duration(is_admin: bool) -> int:
    return OPT_MAX_DURATION_ADMIN if is_admin else OPT_MAX_DURATION


def _opt_hours_label(seconds: int) -> str:
    h = seconds / 3600
    return str(int(h)) if abs(h - int(h)) < 1e-6 else f"{h:.1f}"

_opt_lock = threading.Lock()
_opt_queue: "_queue_mod.Queue[str]" = _queue_mod.Queue()
_opt_current: dict = {"id": None, "proc": None, "cancel": False}
_opt_worker_started = False


def _opt_job_dir(job_id: str) -> Path:
    return OPT_DIR / job_id


def _opt_load(job_id: str) -> Optional[dict]:
    if not OPT_ID_RE.match(job_id or ""):
        return None
    path = _opt_job_dir(job_id) / "job.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _opt_save(job: dict) -> None:
    job["updated"] = time.time()
    d = _opt_job_dir(job["id"])
    if not d.is_dir():
        return
    tmp = d / "job.json.tmp"
    tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / "job.json")


def _opt_update(job_id: str, **fields) -> Optional[dict]:
    with _opt_lock:
        job = _opt_load(job_id)
        if not job:
            return None
        job.update(fields)
        _opt_save(job)
        return job


def _opt_all_jobs() -> list[dict]:
    out = []
    try:
        names = os.listdir(OPT_DIR)
    except OSError:
        return out
    for name in names:
        job = _opt_load(name)
        if job:
            out.append(job)
    return out


def _opt_disk_free() -> int:
    try:
        return shutil.disk_usage(str(UPLOAD_DIR)).free
    except OSError:
        return 0


def _opt_fmt_bytes(n: int) -> str:
    return _bak_fmt_bytes(n)


def _opt_est_need(src_size: int = 0, duration: float = 0, sample_rate: int = 44100,
                  src_on_disk: bool = False) -> int:
    """Download (if not already on disk) + FLAC24 + MP3 320k + 15% scratch + reserve."""
    sr = int(sample_rate or 44100) or 44100
    if duration and duration > 0:
        flac = int(float(duration) * sr * 2 * 3 * 0.7)
        mp3 = int(float(duration) * 40000)
        outputs = int((flac + mp3) * 1.15)
    else:
        outputs = int(max(0, int(src_size or 0)) * 6 * 1.15)
    incoming = 0 if src_on_disk else max(0, int(src_size or 0))
    return incoming + outputs + OPT_DISK_MIN_FREE


def _opt_disk_msg(need: int) -> str:
    free = _opt_disk_free()
    return (
        f"服务器磁盘空间不足（剩余 {_opt_fmt_bytes(free)}，"
        f"本次约需 {_opt_fmt_bytes(need)}，需保留 {_opt_fmt_bytes(OPT_DISK_MIN_FREE)}），请稍后再试"
    )


def _opt_require_disk(need: int, http: bool = True) -> None:
    if _opt_disk_free() < need:
        msg = _opt_disk_msg(need)
        if http:
            raise HTTPException(status_code=507, detail=msg)
        raise RuntimeError(msg)


def _opt_public(job: dict) -> dict:
    keys = (
        "id", "name", "source", "size", "received", "state", "stage", "progress", "target",
        "created", "started", "finished", "error", "duration", "sample_rate", "channels", "codec",
        "before", "after", "gain_db", "outputs", "track_id", "track_status", "imported_format",
        "queue_pos", "trim_end", "tg_chat",
    )
    data = {k: job.get(k) for k in keys}
    base = f"/dj/optimize/{job['id']}/file"
    data["orig_url"] = f"{base}/orig" if job.get("state") not in ("uploading", "downloading") else None
    outs = job.get("outputs") or {}
    data["flac_url"] = f"{base}/flac" if outs.get("flac") else None
    data["mp3_url"] = f"{base}/mp3" if outs.get("mp3") else None
    if job.get("state") == "queued":
        with _opt_lock:
            pending = list(_opt_queue.queue)
        data["queue_pos"] = (pending.index(job["id"]) + 1) if job["id"] in pending else None
    return data


def _opt_owner_ok(job: dict, user: User) -> bool:
    return bool(user.is_admin) or job.get("user_id") == user.id


def _opt_user_or_401(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录后再使用上传优化")
    return user


def _opt_csrf(request: Request) -> None:
    require_csrf(request, request.headers.get("x-csrf-token"))


def _opt_get_owned(job_id: str, user: User) -> dict:
    job = _opt_load(job_id)
    if not job or not _opt_owner_ok(job, user):
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job


_EBUR_SUMMARY_RE = re.compile(r"\[Parsed_ebur128_(\d+) @[^\]]*\] Summary:(.*?)(?=\[Parsed_ebur128_|\[out#|\Z)", re.S)


def _opt_parse_ebur(text: str) -> list[dict]:
    """Return ebur128 summaries ordered by filter index (graph order)."""
    found = []
    for idx, body in _EBUR_SUMMARY_RE.findall(text or ""):
        def grab(pattern):
            m = re.search(pattern, body)
            if not m:
                return None
            try:
                v = float(m.group(1))
            except ValueError:
                return None
            return v if v > -900 else None
        found.append((int(idx), {
            "i": grab(r"I:\s*(-?[\d.]+|-inf)\s*LUFS"),
            "lra": grab(r"LRA:\s*(-?[\d.]+)\s*LU\b"),
            "tp": grab(r"Peak:\s*(-?[\d.]+|-inf)\s*dBFS"),
        }))
    found.sort(key=lambda x: x[0])
    return [f for _, f in found]


def _opt_ffmpeg(job_id: str, args: list[str], duration: float, lo: int, hi: int, stage: str, timeout: float) -> str:
    """Run ffmpeg at low priority with progress; returns stderr text. Raises RuntimeError on failure."""
    cmd = ["nice", "-n", "10", "ionice", "-c", "2", "-n", "7", "ffmpeg", "-hide_banner", "-nostdin", "-y",
           "-progress", "pipe:1", "-nostats"] + args
    _opt_update(job_id, stage=stage, progress=lo)
    err_path = _opt_job_dir(job_id) / "ffmpeg.log"
    start = time.time()
    with err_path.open("w", encoding="utf-8", errors="replace") as err_fh:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_fh, text=True, bufsize=1)
        _opt_current["proc"] = proc
        timed_out = {"v": False}

        def _kill_on_timeout():
            timed_out["v"] = True
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

        watchdog = threading.Timer(timeout, _kill_on_timeout)
        watchdog.daemon = True
        watchdog.start()
        last_push = 0.0
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                if _opt_current.get("cancel"):
                    proc.kill()
                    break
                if time.time() - start > timeout:
                    proc.kill()
                    raise RuntimeError("处理超时")
                if line.startswith("out_time_us=") and duration > 0:
                    try:
                        us = int(line.split("=", 1)[1].strip())
                    except ValueError:
                        continue
                    frac = max(0.0, min(1.0, us / 1e6 / duration))
                    now = time.time()
                    if now - last_push > 1.0:
                        last_push = now
                        _opt_update(job_id, progress=int(lo + (hi - lo) * frac))
            proc.wait(timeout=max(5.0, timeout - (time.time() - start)))
        except subprocess.TimeoutExpired:
            proc.kill()
            raise RuntimeError("处理超时")
        finally:
            watchdog.cancel()
            _opt_current["proc"] = None
    if timed_out["v"]:
        raise RuntimeError("处理超时")
    text = err_path.read_text(encoding="utf-8", errors="replace")
    if _opt_current.get("cancel"):
        raise RuntimeError("任务已取消")
    if proc.returncode != 0:
        tail = text.strip().splitlines()[-3:]
        raise RuntimeError("音频处理失败：" + (" ".join(tail)[-200:] if tail else f"exit {proc.returncode}"))
    _opt_update(job_id, progress=hi)
    return text


def _opt_ff_pos_float(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0 and v != float("inf") else 0.0


def _opt_ffmpeg_clock(path: Path, *, decode: bool, timeout: float) -> float:
    """Duration from the last ffmpeg time= clock. decode=False uses stream copy (packet timestamps)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:a:0"]
    if not decode:
        cmd += ["-c", "copy"]
    cmd += ["-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr or "")
    if not times:
        return 0.0
    h, m, s2 = times[-1]
    return int(h) * 3600 + int(m) * 60 + float(s2)


def _opt_durations_agree(a: float, b: float, slop: float = 2.0) -> bool:
    if a <= 0 or b <= 0:
        return False
    lo, hi = (a, b) if a <= b else (b, a)
    return (hi - lo) <= slop or hi / lo <= 1.02


def _opt_probe(path: Path) -> dict:
    """Read codec/rate plus a trustworthy duration.

    ffprobe format duration is preferred when it agrees with the audio stream duration.
    VBR MP3 without a Xing/LAME header, or webm/ogg from MediaRecorder, may lack or
    mis-estimate duration (bitrate guess). In those cases we confirm with
    `ffmpeg -c copy -f null` (packet timestamps) and, if still suspicious, a decode
    (`ffmpeg -i f -f null -`).
    """
    info = {"duration": 0.0, "sample_rate": 0, "channels": 0, "codec": ""}
    try:
        size = path.stat().st_size
    except OSError:
        return info
    copy_to = min(600.0, 30 + size / (4 * 1024 * 1024))
    dec_to = min(3 * 3600.0, 90 + size / (80 * 1024))  # decode is slower; scale with size
    data: dict = {}
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name,sample_rate,channels,duration,bit_rate:format=duration,bit_rate,size",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(proc.stdout or "{}")
    except Exception:  # noqa: BLE001
        data = {}
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    stream_dur = 0.0
    bit_rate = _opt_ff_pos_float(fmt.get("bit_rate"))
    if streams:
        s = streams[0]
        info["codec"] = str(s.get("codec_name") or "")
        try:
            info["sample_rate"] = int(s.get("sample_rate") or 0)
        except (TypeError, ValueError):
            pass
        try:
            info["channels"] = int(s.get("channels") or 0)
        except (TypeError, ValueError):
            pass
        stream_dur = _opt_ff_pos_float(s.get("duration"))
        if bit_rate <= 0:
            bit_rate = _opt_ff_pos_float(s.get("bit_rate"))
    format_dur = _opt_ff_pos_float(fmt.get("duration"))

    dur = 0.0
    suspicious = False
    if format_dur and stream_dur:
        if _opt_durations_agree(format_dur, stream_dur):
            dur = format_dur
        else:
            suspicious = True
    elif stream_dur:
        dur = stream_dur
    elif format_dur:
        # format-only is often a bitrate estimate (no Xing / no container duration)
        dur = format_dur
        suspicious = True
    if size and bit_rate >= 8000 and dur > 0:
        est = size * 8.0 / bit_rate
        if est > 1 and (dur > est * 2.5 or dur < est / 2.5):
            suspicious = True

    if dur <= 0 or suspicious:
        try:
            copy_dur = _opt_ffmpeg_clock(path, decode=False, timeout=copy_to)
        except Exception:  # noqa: BLE001
            copy_dur = 0.0
        if copy_dur > 0 and dur > 0 and _opt_durations_agree(dur, copy_dur):
            dur = copy_dur
            suspicious = False
        elif copy_dur > 0 and dur <= 0:
            dur = copy_dur
        elif copy_dur > 0:
            dur = copy_dur
            suspicious = True
        if suspicious or dur <= 0:
            try:
                dec = _opt_ffmpeg_clock(path, decode=True, timeout=dec_to)
            except Exception:  # noqa: BLE001
                dec = 0.0
            if dec > 0:
                dur = dec
    info["duration"] = dur
    return info


def _opt_out_rate(sr: int) -> int:
    if sr in (44100, 48000):
        return sr
    if sr and sr % 11025 == 0:
        return 44100
    return 48000


def _opt_process(job_id: str) -> None:
    job = _opt_load(job_id)
    if not job or job.get("state") != "queued":
        return
    d = _opt_job_dir(job_id)
    src = d / f"orig{job['ext']}"
    flac = d / "optimized.flac"
    mp3 = d / "optimized.mp3"
    _opt_update(job_id, state="running", started=time.time(), stage="分析音频", progress=1, error=None)
    try:
        if not src.is_file():
            raise RuntimeError("原文件不存在")
        info = _opt_probe(src)
        dur = float(info["duration"] or 0)
        if not info["codec"]:
            raise RuntimeError("无法识别的音频文件")
        if dur <= 0.5:
            raise RuntimeError("音频太短或无法读取时长")
        # Recorder auto-stop: keep only the first trim_end seconds (drop the repeated / silent tail).
        tin: list[str] = []
        try:
            trim = float(job.get("trim_end") or 0)
        except (TypeError, ValueError):
            trim = 0.0
        if trim >= 1 and trim < dur - 0.2:
            dur = trim
            tin = ["-t", f"{trim:.2f}"]
        cap = _opt_max_duration(bool(job.get("is_admin")))
        if dur > cap:
            raise RuntimeError(f"音频超过 {_opt_hours_label(cap)} 小时上限")
        sr = _opt_out_rate(info["sample_rate"])
        # orig is already on disk. Need FLAC24 + MP3 320k + 15% scratch + reserve.
        _opt_require_disk(_opt_est_need(src_size=src.stat().st_size, duration=dur,
                                        sample_rate=sr, src_on_disk=True), http=False)
        _opt_update(job_id, duration=round(dur, 2), sample_rate=info["sample_rate"], channels=info["channels"],
                    codec=info["codec"], out_rate=sr, progress=3)
        # Per ffmpeg pass (not the whole pipeline). 4× resample + limiter can approach realtime;
        # do not cap at a 3–4h wall clock that would kill a long render.
        timeout = min(16 * 3600, 600 + dur * 4)
        target = float(OPT_TARGETS.get(str(job.get("target")), OPT_TARGETS[OPT_DEFAULT_TARGET]))

        # Pass 1: measure the original and the EQ'd signal in one decode.
        text = _opt_ffmpeg(job_id, [
            *tin, "-i", str(src), "-filter_complex",
            f"[0:a:0]asplit=2[a][b];[a]ebur128=peak=true:framelog=quiet[x];[b]{OPT_EQ},ebur128=peak=true:framelog=quiet[y]",
            "-map", "[x]", "-f", "null", "-", "-map", "[y]", "-f", "null", "-",
        ], dur, 3, 20, "测量响度（第 1 遍）", timeout)
        meas = _opt_parse_ebur(text)
        if len(meas) < 2 or meas[0].get("i") is None or meas[1].get("i") is None:
            raise RuntimeError("音频几乎无声，无法优化")
        before, eq_meas = meas[0], meas[1]
        _opt_update(job_id, before=before)

        def render(gain_db: float, lo: int, hi: int, label: str) -> dict:
            af = (
                f"{OPT_EQ},volume={gain_db:.2f}dB,aresample={sr * 4},"
                f"alimiter=limit={OPT_TP_LIMIT_LINEAR}:attack=2:release=60:level=0:asc=1,aresample={sr}"
            )
            _opt_ffmpeg(job_id, [
                *tin, "-i", str(src), "-map", "0:a:0", "-vn", "-af", af, "-ac", "2", "-ar", str(sr),
                "-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24", "-compression_level", "5",
                str(flac),
            ], dur, lo, hi, label, timeout)
            t2 = _opt_ffmpeg(job_id, [
                "-i", str(flac), "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-",
            ], dur, hi, hi + 6, "复测响度", timeout)
            res = _opt_parse_ebur(t2)
            return res[0] if res else {}

        gain = max(-OPT_MAX_GAIN_DB, min(OPT_MAX_GAIN_DB, target - float(eq_meas["i"])))
        after = render(gain, 20, 52, "均衡 + 响度 + 限幅（第 2 遍）")
        # The limiter eats some loudness on dense material; one correction pass if we are clearly short.
        if after.get("i") is not None and target - after["i"] > 0.4 and gain < OPT_MAX_GAIN_DB:
            gain = min(OPT_MAX_GAIN_DB, gain + (target - after["i"]))
            after = render(gain, 58, 72, "响度微调")
        _opt_ffmpeg(job_id, [
            "-i", str(flac), "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", "320k", "-ar", str(sr),
            "-map_metadata", "-1", str(mp3),
        ], dur, 78, 95, "导出 MP3 320k", timeout)
        t3 = _opt_ffmpeg(job_id, ["-i", str(mp3), "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"],
                         dur, 95, 99, "校验 MP3", timeout)
        mp3_meas = (_opt_parse_ebur(t3) or [{}])[0]
        after["mp3_tp"] = mp3_meas.get("tp")
        outputs = {"flac": flac.stat().st_size, "mp3": mp3.stat().st_size}
        _opt_update(job_id, state="done", stage="完成", progress=100, after=after, gain_db=round(gain, 2),
                    outputs=outputs, finished=time.time())
        # Telegram originals are no longer needed once outputs exist; the library copy is created on import.
        if (job or {}).get("source") == "telegram":
            _opt_drop_orig(job_id)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) if isinstance(exc, RuntimeError) else "处理失败，请换一个文件再试"
        for p in (flac, mp3):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        _opt_update(job_id, state="failed", stage="失败", error=msg[:300], finished=time.time())
    finally:
        try:
            (d / "ffmpeg.log").unlink(missing_ok=True)
        except OSError:
            pass


def _opt_purge_job_dir(job_id: str) -> None:
    if not OPT_ID_RE.match(job_id or ""):
        return
    shutil.rmtree(_opt_job_dir(job_id), ignore_errors=True)


def _opt_drop_orig(job_id: str) -> None:
    d = _opt_job_dir(job_id)
    for p in d.glob("orig.*"):
        try:
            p.unlink()
        except OSError:
            pass


def _opt_library_exists(job: dict) -> bool:
    tid = job.get("track_id") if job else None
    if not tid:
        return False
    db = SessionLocal()
    try:
        track = db.get(Track, int(tid))
        return bool(track and track.filename and (UPLOAD_DIR / track.filename).is_file())
    except (TypeError, ValueError):
        return False
    finally:
        db.close()


def _opt_cleanup() -> None:
    now = time.time()
    try:
        names = os.listdir(OPT_DIR)
    except OSError:
        return
    for name in names:
        d = OPT_DIR / name
        if not d.is_dir() or not OPT_ID_RE.match(name):
            continue
        if _opt_current.get("id") == name:
            continue
        job = _opt_load(name)
        try:
            age_ref = float((job or {}).get("updated") or d.stat().st_mtime)
            created = float((job or {}).get("created") or age_ref)
        except (TypeError, ValueError, OSError):
            continue
        state = (job or {}).get("state")
        imported = bool(job and job.get("track_id") and _opt_library_exists(job))
        stale_upload = state in ("uploading", "uploaded", "downloading") and now - age_ref > OPT_UPLOAD_STALE
        if job is None and now - age_ref > OPT_UPLOAD_STALE:
            stale_upload = True
        failed_expired = state == "failed" and now - created > OPT_FAILED_KEEP_SECONDS
        expired = now - created > OPT_KEEP_SECONDS and state not in ("queued", "running", "downloading")
        if imported or stale_upload or failed_expired or expired:
            shutil.rmtree(d, ignore_errors=True)
            continue
        if state == "done" and (job or {}).get("source") == "telegram":
            _opt_drop_orig(name)


def _opt_worker() -> None:
    last_clean = 0.0
    while True:
        try:
            job_id = _opt_queue.get(timeout=300)
        except _queue_mod.Empty:
            job_id = None
        if job_id:
            _opt_current.update(id=job_id, cancel=False)
            try:
                _opt_process(job_id)
            except Exception:  # noqa: BLE001
                pass
            finally:
                _opt_current.update(id=None, proc=None, cancel=False)
        if time.time() - last_clean > 1800:
            last_clean = time.time()
            try:
                _opt_cleanup()
            except Exception:  # noqa: BLE001
                pass


def opt_start_worker() -> None:
    global _opt_worker_started
    if _opt_worker_started:
        return
    _opt_worker_started = True
    OPT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _opt_cleanup()
    except Exception:  # noqa: BLE001
        pass
    # Jobs interrupted by a restart are re-queued (the original upload is still on disk).
    jobs = sorted(_opt_all_jobs(), key=lambda j: float(j.get("created") or 0))
    for job in jobs:
        if job.get("state") in ("queued", "running"):
            _opt_update(job["id"], state="queued", stage="排队中（服务重启后继续）", progress=0)
            _opt_queue.put(job["id"])
    threading.Thread(target=_opt_worker, name="dj-optimize", daemon=True).start()


def _opt_name_clean(name: str) -> str:
    name = Path(str(name or "")).name.strip().replace("\x00", "")
    name = re.sub(r"[\r\n\t]+", " ", name)
    return name[:200] or "audio"


@app.get("/dj/optimize/jobs")
def opt_jobs(request: Request, db: Session = Depends(get_db)):
    user = _opt_user_or_401(request, db)
    # Imported jobs are deleted as soon as the library copy exists; any leftover is hidden here.
    jobs = [j for j in _opt_all_jobs() if j.get("user_id") == user.id and not j.get("track_id")]
    jobs.sort(key=lambda j: float(j.get("created") or 0), reverse=True)
    return {"jobs": [_opt_public(j) for j in jobs[:20]]}


@app.post("/dj/optimize/init")
async def opt_init(request: Request, db: Session = Depends(get_db)):
    _opt_csrf(request)
    user = _opt_user_or_401(request, db)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = {}
    name = _opt_name_clean(payload.get("name"))
    ext = Path(name).suffix.lower()
    if ext not in OPT_EXTS:
        raise HTTPException(status_code=400, detail="仅支持 MP3 / WAV / FLAC / M4A / OGG / AAC / WEBM 音频")
    try:
        size = int(payload.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        raise HTTPException(status_code=400, detail="文件是空的")
    max_bytes = _opt_max_bytes(bool(user.is_admin))
    if size > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件不能超过 {max_bytes // (1024 * 1024)}MB")
    source = "record" if payload.get("source") == "record" else "upload"
    target = str(payload.get("target") or OPT_DEFAULT_TARGET)
    if target not in OPT_TARGETS:
        target = OPT_DEFAULT_TARGET
    trim_end = None
    try:
        tv = float(payload.get("trim_end") or 0)
        if math.isfinite(tv) and 1 <= tv <= _opt_max_duration(bool(user.is_admin)):
            trim_end = round(tv, 2)
    except (TypeError, ValueError):
        trim_end = None
    _opt_require_disk(_opt_est_need(src_size=size, src_on_disk=False), http=True)
    now = time.time()
    if not user.is_admin:
        mine = [j for j in _opt_all_jobs() if j.get("user_id") == user.id]
        if sum(1 for j in mine if j.get("state") in OPT_ACTIVE_STATES) >= OPT_USER_ACTIVE_MAX:
            raise HTTPException(status_code=429, detail="你已有任务在处理中，请等它完成后再提交")
        if sum(1 for j in mine if now - float(j.get("created") or 0) < 86400) >= OPT_USER_DAILY_MAX:
            raise HTTPException(status_code=429, detail=f"每天最多优化 {OPT_USER_DAILY_MAX} 个文件")
    job_id = secrets.token_hex(12)
    d = _opt_job_dir(job_id)
    d.mkdir(parents=True, exist_ok=False)
    (d / f"orig{ext}").touch()
    job = {
        "id": job_id, "user_id": user.id, "is_admin": bool(user.is_admin), "name": name, "ext": ext,
        "source": source, "size": size, "received": 0, "state": "uploading", "stage": "上传中",
        "progress": 0, "target": target, "created": now,
    }
    if trim_end:
        job["trim_end"] = trim_end
    with _opt_lock:
        _opt_save(job)
    return {"id": job_id, "chunk": OPT_CHUNK_BYTES}


@app.post("/dj/optimize/{job_id}/chunk")
async def opt_chunk(job_id: str, request: Request, offset: int = 0, db: Session = Depends(get_db)):
    _opt_csrf(request)
    user = _opt_user_or_401(request, db)
    job = _opt_get_owned(job_id, user)
    if job.get("state") != "uploading":
        raise HTTPException(status_code=409, detail="该任务不在上传状态")
    path = _opt_job_dir(job_id) / f"orig{job['ext']}"
    current = path.stat().st_size if path.exists() else 0
    if offset != current:
        return JSONResponse({"detail": "offset mismatch", "received": current}, status_code=409)
    total = int(job["size"])
    written = 0
    with path.open("ab") as fh:
        async for part in request.stream():
            if not part:
                continue
            written += len(part)
            if written > OPT_CHUNK_MAX or current + written > total:
                fh.truncate(current)
                raise HTTPException(status_code=413, detail="分片过大或超出文件大小")
            fh.write(part)
    received = current + written
    state = "uploaded" if received >= total else "uploading"
    _opt_update(job_id, received=received, state=state, progress=0,
                stage="上传完成" if state == "uploaded" else "上传中")
    return {"received": received, "state": state}


@app.post("/dj/optimize/{job_id}/start")
def opt_start(job_id: str, request: Request, db: Session = Depends(get_db)):
    _opt_csrf(request)
    user = _opt_user_or_401(request, db)
    job = _opt_get_owned(job_id, user)
    if job.get("state") not in ("uploaded", "failed"):
        raise HTTPException(status_code=409, detail="文件还没上传完成")
    path = _opt_job_dir(job_id) / f"orig{job['ext']}"
    if not path.is_file() or path.stat().st_size != int(job.get("size") or -1):
        raise HTTPException(status_code=409, detail="文件不完整，请重新上传")
    _opt_require_disk(_opt_est_need(src_size=int(job.get("size") or 0), src_on_disk=True), http=True)
    if not user.is_admin and _opt_queue.qsize() >= OPT_QUEUE_MAX:
        raise HTTPException(status_code=429, detail="排队任务较多，请稍后再试")
    job = _opt_update(job_id, state="queued", stage="排队中", progress=0, error=None) or job
    _opt_queue.put(job_id)
    return _opt_public(job)


@app.get("/dj/optimize/{job_id}")
def opt_status(job_id: str, request: Request, db: Session = Depends(get_db)):
    user = _opt_user_or_401(request, db)
    return _opt_public(_opt_get_owned(job_id, user))


@app.get("/dj/optimize/{job_id}/file/{kind}")
def opt_file(job_id: str, kind: str, request: Request, download: int = 0, db: Session = Depends(get_db)):
    user = _opt_user_or_401(request, db)
    job = _opt_get_owned(job_id, user)
    d = _opt_job_dir(job_id)
    stem = Path(job.get("name") or "audio").stem[:120] or "audio"
    if kind == "orig":
        if job.get("state") == "uploading":
            raise HTTPException(status_code=404, detail="文件还在上传")
        path, media, fname = d / f"orig{job['ext']}", OPT_MIME.get(job["ext"], "application/octet-stream"), f"{stem}{job['ext']}"
    elif kind == "flac":
        path, media, fname = d / "optimized.flac", "audio/flac", f"{stem}-优化版.flac"
    elif kind == "mp3":
        path, media, fname = d / "optimized.mp3", "audio/mpeg", f"{stem}-优化版-320k.mp3"
    else:
        raise HTTPException(status_code=404, detail="文件不存在")
    if kind != "orig" and job.get("state") != "done":
        raise HTTPException(status_code=404, detail="还没处理完成")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在或已过期")
    return FileResponse(path, media_type=media, filename=fname,
                        content_disposition_type="attachment" if download else "inline")


@app.post("/dj/optimize/{job_id}/import")
async def opt_import(job_id: str, request: Request, db: Session = Depends(get_db)):
    _opt_csrf(request)
    user = _opt_user_or_401(request, db)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = {}
    with _opt_lock:
        job = _opt_load(job_id)
        if not job or not _opt_owner_ok(job, user):
            raise HTTPException(status_code=404, detail="任务不存在或已过期")
        if job.get("state") != "done":
            raise HTTPException(status_code=409, detail="还没处理完成")
        if job.get("track_id"):
            raise HTTPException(status_code=409, detail="已经导入过曲库了")
        # Library imports always store the optimized MP3 320k (FLAC remains downloadable from the job).
        fmt = "mp3"
        src = _opt_job_dir(job_id) / f"optimized.{fmt}"
        if not src.is_file():
            raise HTTPException(status_code=404, detail="文件不存在或已过期")
        title = str(payload.get("title") or "").strip()
        title = re.sub(r"[\r\n\t]+", " ", title)[:200] or (Path(job.get("name") or "").stem + "（优化版）")[:200]
        ext = f".{fmt}"
        stored = unique_upload_name(ext)
        dest = UPLOAD_DIR / stored
        shutil.copyfile(src, dest)
        dest.chmod(0o644)
        status = TRACK_STATUS_APPROVED if user.is_admin else TRACK_STATUS_PENDING
        track = Track(
            title=title, filename=stored, original_name=(title + ext)[:255], uploader_id=user.id,
            status=status, pack_name=None, content_hash=file_sha256(dest), file_size=dest.stat().st_size,
        )
        keep_flac = payload.get("keep_flac")
        if keep_flac is None:
            keep_flac = bool(user.is_admin)
        if keep_flac:
            flac_src = _opt_job_dir(job_id) / "optimized.flac"
            if flac_src.is_file():
                try:
                    fname, fsize = dj_flac.attach_flac(flac_src, UPLOAD_DIR)
                    track.flac_path = fname
                    track.flac_size = fsize
                    track.flac_pinned = False
                    track.flac_added_at = datetime.now(timezone.utc).replace(tzinfo=None)
                except Exception:  # noqa: BLE001
                    logging.exception("keep FLAC on import failed for job %s", job_id)
        db.add(track)
        try:
            db.commit()
        except Exception:
            db.rollback()
            dest.unlink(missing_ok=True)
            if getattr(track, "flac_path", None):
                dj_flac.drop_track_flac(track, UPLOAD_DIR, FLAC_SNAP_ROOTS)
            raise
        try:
            if track.flac_path:
                dj_flac.enforce_flac_quota(
                    db, db.scalars(select(Track)).all(), UPLOAD_DIR, FLAC_SNAP_ROOTS,
                    dj_flac.flac_quota_bytes(lambda k, d="": get_setting(db, k, d)),
                    OPT_DISK_MIN_FREE,
                    lambda k, d="": get_setting(db, k, d),
                    lambda k, v: set_setting(db, k, v),
                    remote_delete=r2_delete_key,
                )
                db.commit()
        except Exception:  # noqa: BLE001
            logging.exception("flac quota enforce after import failed")
            db.rollback()
        job["track_id"] = track.id
        job["track_status"] = status
        job["imported_format"] = fmt
        _opt_save(job)
        if job.get("source") == "telegram":
            try:
                tga_done_mark(job.get("tg_chat_id"), job.get("tg_msg"), downloaded=True, track_id=track.id)
            except Exception:  # noqa: BLE001
                pass
        # Library copy is now the source of truth; drop the optimize job files immediately.
        _opt_purge_job_dir(job_id)
        schedule_r2_upload(track.id)
    msg = "已导入曲库并发布" if status == TRACK_STATUS_APPROVED else "已提交到曲库，管理员审核通过后显示"
    return {"ok": True, "track_id": track.id, "status": status, "message": msg}


@app.post("/dj/optimize/{job_id}/delete")
def opt_delete(job_id: str, request: Request, db: Session = Depends(get_db)):
    _opt_csrf(request)
    user = _opt_user_or_401(request, db)
    job = _opt_get_owned(job_id, user)
    if _opt_current.get("id") == job_id:
        _opt_current["cancel"] = True
        proc = _opt_current.get("proc")
        if proc:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        for _ in range(50):
            if _opt_current.get("id") != job_id:
                break
            time.sleep(0.1)
    with _opt_lock:
        try:
            pending = list(_opt_queue.queue)
            if job_id in pending:
                _opt_queue.queue.remove(job_id)
        except ValueError:
            pass
    shutil.rmtree(_opt_job_dir(job["id"]), ignore_errors=True)
    return {"ok": True}



# ---------------------------------------------------------------------------
# Source-channel comment text sync (public discussion widget, no login)
# ---------------------------------------------------------------------------
TGC_PAGE_LIMIT = 100
TGC_MAX_PAGES = 30
TGC_REQ_PAUSE = 1.1            # ~1 request per second, single thread
TGC_ERROR_PAUSE = 15 * 60
TGC_RECENT_DAYS = 7
TGC_SHOW = 20
TGC_TEXT_MAX = 1000
TGC_MEDIA_PLACEHOLDER = "[图片/贴纸]"
TGC_MEDIA_RE = re.compile(
    r'class="tgme_widget_message_(?:photo|sticker|video|voice|document|roundvideo|animated|gif|poll|location|contact)',
)
TGC_URL_RE = re.compile(r"(?:https?://|www\.|t\.me/|telegram\.me/)\S+", re.I)
TGC_SOURCE_RE = re.compile(r"t\.me/(?:s/)?([A-Za-z0-9_]+)/(\d+)")

_tgc_event = threading.Event()
_tgc_state: dict = {
    "running": False, "phase": "", "done": 0, "total": 0, "paused_until": 0.0,
    "error": "", "last_cycle": "", "started": False, "requests": 0,
}


class TgcFetchError(Exception):
    pass


def _tgc_http(url: str, data: Optional[bytes] = None, headers: Optional[dict] = None) -> str:
    h = {"User-Agent": TG_USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
    _tgc_state["requests"] += 1
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.read(4 * 1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        wait = TGC_ERROR_PAUSE
        if exc.code == 429:
            try:
                wait = max(wait, int(exc.headers.get("Retry-After") or 0))
            except ValueError:
                pass
            wait = max(wait, 30 * 60)
        raise TgcFetchError(f"HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001
        raise TgcFetchError(type(exc).__name__) from exc


def _tgc_html_text(fragment: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", fragment or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_lib.unescape(s).replace("\u00a0", " ")
    s = re.sub(r"[ \t]+\n", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def parse_tg_comments(fragment: str) -> list[dict]:
    out = []
    for blk in re.split(r'(?=<div class="tgme_widget_message_wrap)', fragment or ""):
        if 'class="tgme_widget_message_wrap' not in blk:
            continue
        m = re.search(r'data-post-id="(\d+)"', blk)
        if not m:
            continue
        cid = int(m.group(1))
        # Drop the hidden "reply to this" template; it repeats the author/text.
        body = re.sub(r'<div class="tgme_widget_message_reply_template.*?</div>\s*</div>', "", blk, flags=re.S)
        am = re.search(r'class="tgme_widget_message_author_name"[^>]*>(.*?)</(?:a|span)>\s*</div>|class="tgme_widget_message_author_name"[^>]*>(.*?)</', body, re.S)
        # Telegram sometimes blanks names in the public widget; keep "" so a known name is not overwritten.
        author = _tgc_html_text((am.group(1) or am.group(2)) if am else "")[:80]
        tm = re.search(r'<div class="tgme_widget_message_text js-message_text"[^>]*>(.*?)</div>', body, re.S)
        text_v = _tgc_html_text(tm.group(1)) if tm else ""
        media = bool(TGC_MEDIA_RE.search(body))
        rm = re.search(r'data-reply-to="(\d+)"', body)
        dm = re.search(r'<time datetime="([^"]+)"', body)
        posted = None
        if dm:
            try:
                posted = datetime.fromisoformat(dm.group(1)).astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError:
                posted = None
        out.append({
            "comment_id": cid, "author": author, "text": text_v, "media": media,
            "reply_to": int(rm.group(1)) if rm else None, "posted_at": posted,
        })
    return out


def fetch_tg_thread(post_id: int) -> tuple[int, list[dict], bool]:
    """Return (tg_count, comments, complete). complete=False if paging was capped."""
    url = f"https://t.me/{TG_CHANNEL}/{int(post_id)}?embed=1&discussion=1&comments_limit={TGC_PAGE_LIMIT}"
    page = _tgc_http(url)
    im = re.search(r'Discussion\.init\(\{[^}]*"comments_cnt":(\d+)', page)
    if not im:
        return 0, [], True  # comments disabled / post missing
    total = int(im.group(1))
    comments = parse_tg_comments(page)
    seen = {c["comment_id"] for c in comments}
    before = re.search(r'data-before="(\d+)"', page)
    pages = 1
    if before and len(seen) < total:
        api_m = re.search(r'"api_url":"([^"]+)"', page)
        form = {}
        for k in ("peer", "top_msg_id", "discussion_hash"):
            fm = re.search(r'name="%s" value="([^"]*)"' % k, page)
            if fm:
                form[k] = html_lib.unescape(fm.group(1))
        if api_m and len(form) == 3:
            api = json.loads('"' + api_m.group(1) + '"')
            while before and len(seen) < total and pages < TGC_MAX_PAGES:
                time.sleep(TGC_REQ_PAUSE)
                body = urllib.parse.urlencode({**form, "before_id": before.group(1), "method": "loadComments"}).encode()
                raw = _tgc_http(api, body, {
                    "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest",
                    "Origin": "https://t.me", "Referer": url,
                })
                pages += 1
                try:
                    data = json.loads(raw)
                except ValueError as exc:
                    raise TgcFetchError("bad json") from exc
                chunk = data.get("comments_html") or ""
                new = [c for c in parse_tg_comments(chunk) if c["comment_id"] not in seen]
                if not new:
                    break
                comments.extend(new)
                seen.update(c["comment_id"] for c in new)
                before = re.search(r'data-before="(\d+)"', chunk)
    complete = len(seen) >= total or not before
    return total, comments, complete


def tgc_clean_text(fctx: dict, c: dict) -> Optional[str]:
    raw = c.get("text") or ""
    if raw and tg_block_hit(fctx, raw):
        return None
    txt = raw
    if txt:
        txt, _hits = tg_strip_text(fctx, txt)
        txt = TGC_URL_RE.sub("", txt)
        txt = TG_HANDLE_RE.sub("", txt)
        txt = re.sub(r"[ \t]{2,}", " ", txt).strip()
    if not txt and c.get("media"):
        txt = TGC_MEDIA_PLACEHOLDER
    elif txt and c.get("media"):
        txt = f"{txt} {TGC_MEDIA_PLACEHOLDER}"
    return txt[:TGC_TEXT_MAX] if txt else None


def tgc_published_map(db: Session) -> dict[int, set[int]]:
    """post_id -> announcement ids, for published announcements from the source channel."""
    out: dict[int, set[int]] = {}
    for aid, src in db.execute(select(Announcement.id, Announcement.source_url).where(Announcement.source_url.is_not(None))).all():
        m = TGC_SOURCE_RE.search(src or "")
        if m and m.group(1).lower() == TG_CHANNEL.lower():
            out.setdefault(int(m.group(2)), set()).add(aid)
    for pid, aid in db.execute(
        select(TgPost.post_id, TgPost.announcement_id).where(
            TgPost.channel == TG_CHANNEL, TgPost.announcement_id.is_not(None)
        )
    ).all():
        out.setdefault(int(pid), set()).add(int(aid))
    if out:
        live = set(db.scalars(select(Announcement.id)).all())
        out = {p: {a for a in s if a in live} for p, s in out.items()}
        out = {p: s for p, s in out.items() if s}
    return out


def ann_tg_post_id(db: Session, ann: Announcement) -> Optional[int]:
    m = TGC_SOURCE_RE.search(ann.source_url or "")
    if m and m.group(1).lower() == TG_CHANNEL.lower():
        return int(m.group(2))
    pid = db.scalar(select(TgPost.post_id).where(TgPost.channel == TG_CHANNEL, TgPost.announcement_id == ann.id).limit(1))
    return int(pid) if pid else None


def tgc_sync_post(db: Session, post_id: int, ann_ids: set[int], fctx: dict) -> int:
    total, comments, complete = fetch_tg_thread(post_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    existing = {
        r.comment_id: r for r in db.scalars(
            select(TgComment).where(TgComment.channel == TG_CHANNEL, TgComment.post_id == post_id)
        ).all()
    }
    keep: set[int] = set()
    for c in comments:
        txt = tgc_clean_text(fctx, c)
        if not txt:
            continue
        keep.add(c["comment_id"])
        row = existing.get(c["comment_id"])
        if row is None:
            row = TgComment(channel=TG_CHANNEL, post_id=post_id, comment_id=c["comment_id"])
            db.add(row)
            existing[c["comment_id"]] = row
        if c["author"] or not row.author:
            row.author = c["author"] or ""
        row.text = txt
        row.reply_to = c["reply_to"]
        row.posted_at = c["posted_at"]
        row.synced_at = now
    fetched_ids = {c["comment_id"] for c in comments}
    min_seen = min(fetched_ids) if fetched_ids else None
    for cid, row in list(existing.items()):
        if cid in keep:
            continue
        # Deleted on the source, or now filtered. If paging was capped, only judge the range we saw.
        if complete or (min_seen is not None and cid >= min_seen) or cid in fetched_ids:
            db.delete(row)
    db.flush()
    stored = db.scalar(
        select(func.count()).select_from(TgComment).where(TgComment.channel == TG_CHANNEL, TgComment.post_id == post_id)
    ) or 0
    th = db.scalar(select(TgCommentThread).where(TgCommentThread.channel == TG_CHANNEL, TgCommentThread.post_id == post_id))
    if th is None:
        th = TgCommentThread(channel=TG_CHANNEL, post_id=post_id)
        db.add(th)
    th.tg_count = total
    th.stored = stored
    th.synced_at = now
    th.note = None if complete else "partial"
    if ann_ids:
        db.execute(
            text("UPDATE announcements SET tg_comment_count = :n WHERE id IN (" + ",".join(str(int(a)) for a in ann_ids) + ")"),
            {"n": stored},
        )
    db.execute(
        text("UPDATE tg_posts SET tg_comment_count = :n WHERE channel = :c AND post_id = :p"),
        {"n": stored, "c": TG_CHANNEL, "p": post_id},
    )
    db.commit()
    return stored


def tgc_targets(db: Session) -> tuple[list[tuple[str, int]], int, int]:
    pmap = tgc_published_map(db)
    threads = {
        int(p): s for p, s in db.execute(
            select(TgCommentThread.post_id, TgCommentThread.synced_at).where(TgCommentThread.channel == TG_CHANNEL)
        ).all()
    }
    posted = {
        int(p): t for p, t in db.execute(
            select(TgPost.post_id, TgPost.posted_at).where(TgPost.channel == TG_CHANNEL)
        ).all()
    }
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    recent, backfill, daily = [], [], []
    for pid in sorted(pmap, reverse=True):
        synced = threads.get(pid)
        pt = posted.get(pid)
        age = (now - pt).total_seconds() if pt else None
        since = (now - synced).total_seconds() if synced else None
        if age is not None and age < TGC_RECENT_DAYS * 86400:
            need = since is None or since > (20 * 60 if age < 86400 else 6 * 3600)
            if need:
                recent.append(("recent", pid))
        elif synced is None:
            backfill.append(("backfill", pid))
        elif since is not None and since > 24 * 3600:
            daily.append(("daily", pid))
    done = sum(1 for p in pmap if p in threads)
    return recent + backfill + daily, done, len(pmap)


def tgc_cycle() -> None:
    if time.time() < float(_tgc_state.get("paused_until") or 0):
        return
    db = SessionLocal()
    try:
        if get_setting(db, "tgc_enabled", "0") != "1":
            _tgc_state["phase"] = "未开启"
            return
        _tgc_state["running"] = True
        targets, done, total = tgc_targets(db)
        _tgc_state.update(done=done, total=total)
        if not targets:
            _tgc_state["phase"] = "已是最新"
            return
        fctx = tg_rules_ctx(db)
        pmap = tgc_published_map(db)
        synced = 0
        for kind, pid in targets:
            _tgc_state["phase"] = {"recent": "同步近 7 天", "backfill": "补全已发布公告", "daily": "每日复查"}[kind]
            try:
                tgc_sync_post(db, pid, pmap.get(pid, set()), fctx)
            except TgcFetchError as exc:
                db.rollback()
                _tgc_state["error"] = f"{datetime.now(TZ_CN).strftime('%m-%d %H:%M')} 原帖评论拉取失败（{exc}），暂停 15 分钟后自动继续"
                _tgc_state["paused_until"] = time.time() + (30 * 60 if "429" in str(exc) else TGC_ERROR_PAUSE)
                break
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                _tgc_state["error"] = f"帖子 {pid} 解析失败：{type(exc).__name__}"
            synced += 1
            if kind == "backfill":
                _tgc_state["done"] = _tgc_state.get("done", 0) + 1
            if synced % 20 == 0:
                fctx = tg_rules_ctx(db)
            if _tgc_event.is_set() and kind != "recent":
                # A fresh collect just finished: go back and do recent posts first.
                break
            time.sleep(TGC_REQ_PAUSE)
        else:
            _tgc_state["error"] = ""
        now_txt = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")
        _tgc_state["last_cycle"] = f"{now_txt}（同步 {synced} 帖）"
        set_setting(db, "tgc_last_sync", _tgc_state["last_cycle"])
    finally:
        _tgc_state["running"] = False
        db.close()


def tgc_worker() -> None:
    time.sleep(20)
    while True:
        _tgc_event.clear()
        try:
            tgc_cycle()
        except Exception:  # noqa: BLE001
            pass
        _tgc_event.wait(timeout=120)


def tgc_start() -> None:
    if _tgc_state.get("started") or os.environ.get("DJ666_TGC_DISABLE") == "1":
        return
    _tgc_state["started"] = True
    threading.Thread(target=tgc_worker, name="tg-comments", daemon=True).start()


def tgc_status_text(db: Session) -> str:
    threads = db.scalar(select(func.count()).select_from(TgCommentThread)) or 0
    stored = db.scalar(select(func.count()).select_from(TgComment)) or 0
    done, total = _tgc_state.get("done", 0), _tgc_state.get("total", 0)
    last = _tgc_state.get("last_cycle") or get_setting(db, "tgc_last_sync", "") or "尚未同步"
    line = f"原帖评论同步：已同步 {threads} 帖、{stored} 条评论"
    if total:
        line += f" · 已发布公告补全 {min(done, total)}/{total}"
    line += f" · 上次同步：{last}"
    if _tgc_state.get("running"):
        line += f" · 进行中（{_tgc_state.get('phase') or ''}）"
    if _tgc_state.get("error"):
        line += f" · {_tgc_state['error']}"
    return line


@app.get("/announcements/{ann_id}/tg-comments")
def ann_tg_comments(ann_id: int, request: Request, offset: int = 0, db: Session = Depends(get_db)):
    ann = db.get(Announcement, ann_id)
    if not ann:
        return JSONResponse({"ok": False, "items": [], "total": 0}, status_code=404)
    pid = ann_tg_post_id(db, ann)
    if not pid:
        return {"ok": True, "items": [], "total": 0, "next": None}
    offset = max(0, int(offset or 0))
    base = select(TgComment).where(TgComment.channel == TG_CHANNEL, TgComment.post_id == pid)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(TgComment.posted_at.asc(), TgComment.comment_id.asc()).offset(offset).limit(TGC_SHOW)).all()
    reply_ids = {r.reply_to for r in rows if r.reply_to}
    names = {}
    if reply_ids:
        names = dict(db.execute(
            select(TgComment.comment_id, TgComment.author).where(
                TgComment.channel == TG_CHANNEL, TgComment.comment_id.in_(reply_ids)
            )
        ).all())
    items = []
    for r in rows:
        ts = r.posted_at.replace(tzinfo=timezone.utc).timestamp() if r.posted_at else 0
        items.append({
            "id": r.comment_id, "author": r.author or "网友", "text": r.text,
            "time": fmt_time(r.posted_at), "ts": ts,
            "reply": names.get(r.reply_to) if r.reply_to else None,
        })
    nxt = offset + len(rows)
    return {"ok": True, "items": items, "total": total, "next": nxt if nxt < total else None}


# ---------------------------------------------------------------------------
# Telegram 音乐导入 (admin only): a Telethon *user* session lists audio in a channel the admin can
# read, downloads one file at a time into an optimize job, then the normal optimize pipeline runs.
# Secrets: api_id/api_hash in data/tg_user.json (0600); the session string is Fernet-encrypted in
# data/tg_session.enc with the key in data/tg_session.key (0600). Phone / code / 2FA password /
# session are never logged, stored in the DB or echoed back.
# ---------------------------------------------------------------------------
TGA_CFG_PATH = DATA_DIR / "tg_user.json"
TGA_KEY_PATH = DATA_DIR / "tg_session.key"
TGA_SESSION_PATH = DATA_DIR / "tg_session.enc"
TGA_STATE_PATH = DATA_DIR / "tga_state.json"
TGA_BATCH_PATH = DATA_DIR / "tga_batch.json"
TGA_DAILY_DEFAULT = 30
TGA_DAILY_MAX = 200
TGA_LIST_MIN_GAP = 5.0          # seconds between Telegram list/search/resolve calls
TGA_PAGE = 50
TGA_FLOOD_AUTO_WAIT = 120       # a FloodWait shorter than this during a download is slept through
TGA_RATE = {"send": (5, 3600), "signin": (10, 3600)}
TGA_PHONE_RE = re.compile(r"^\+?[0-9]{6,16}$")

_tga_lock = threading.RLock()
_tga_api_lock = threading.Lock()
_tga = {
    "loop": None, "thread": None, "client": None, "client_cfg": None,
    "login": None,            # {"phone", "hash", "stage", "ts"} – memory only
    "me": None, "authorized": None, "checked": 0.0,
    "flood_until": 0.0, "last_api": 0.0,
    "download": None,         # {"job_id", "name", "size", "received", "state", "error"}
    "batch": None,            # persisted queue in data/tga_batch.json
    "attempts": {"send": [], "signin": []},
    "entities": {},
}
logging.getLogger("telethon").setLevel(logging.WARNING)


def _tga_write_private(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(path))


def tga_cfg_load() -> dict:
    """Config file holds daily_cap in clear and api_id/api_hash only as a Fernet token ("enc")."""
    try:
        raw = json.loads(TGA_CFG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    cfg = {"daily_cap": raw.get("daily_cap")}
    enc = raw.get("enc")
    if enc:
        try:
            sec = json.loads(tga_decrypt(enc.encode("ascii")))
            cfg["api_id"], cfg["api_hash"] = sec.get("api_id"), sec.get("api_hash")
        except Exception:  # noqa: BLE001 – unreadable secrets count as "not configured"
            pass
    return cfg


def tga_cfg_save(cfg: dict) -> None:
    out: dict = {}
    if cfg.get("daily_cap"):
        out["daily_cap"] = cfg["daily_cap"]
    if cfg.get("api_id") and cfg.get("api_hash"):
        sec = json.dumps({"api_id": int(cfg["api_id"]), "api_hash": str(cfg["api_hash"])})
        out["enc"] = tga_encrypt(sec).decode("ascii")
    _tga_write_private(TGA_CFG_PATH, json.dumps(out).encode("utf-8"))


def tga_configured(cfg: Optional[dict] = None) -> bool:
    cfg = tga_cfg_load() if cfg is None else cfg
    return bool(cfg.get("api_id")) and bool(cfg.get("api_hash"))


def tga_daily_cap(cfg: Optional[dict] = None) -> int:
    cfg = tga_cfg_load() if cfg is None else cfg
    try:
        return max(1, min(TGA_DAILY_MAX, int(cfg.get("daily_cap") or TGA_DAILY_DEFAULT)))
    except (TypeError, ValueError):
        return TGA_DAILY_DEFAULT


def _tga_fernet():
    from cryptography.fernet import Fernet
    with _tga_lock:
        try:
            key = TGA_KEY_PATH.read_bytes().strip()
        except OSError:
            key = b""
        if not key:
            key = Fernet.generate_key()
            _tga_write_private(TGA_KEY_PATH, key)
        return Fernet(key)


def tga_encrypt(text: str) -> bytes:
    return _tga_fernet().encrypt(text.encode("utf-8"))


def tga_decrypt(blob: bytes) -> str:
    return _tga_fernet().decrypt(blob).decode("utf-8")


def tga_session_load() -> str:
    try:
        blob = TGA_SESSION_PATH.read_bytes()
    except OSError:
        return ""
    try:
        return tga_decrypt(blob)
    except Exception:  # noqa: BLE001 – wrong key / corrupted: treat as logged out
        return ""


def tga_session_save(session_str: str) -> None:
    _tga_write_private(TGA_SESSION_PATH, tga_encrypt(session_str))


def tga_session_delete() -> None:
    try:
        TGA_SESSION_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _tga_today() -> str:
    return datetime.now(timezone.utc).astimezone(TZ_CN).strftime("%Y-%m-%d")


def _tga_state() -> dict:
    try:
        st = json.loads(TGA_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    if st.get("day") != _tga_today():
        st = {"day": _tga_today(), "count": 0}
    return st


def _tga_count_today() -> int:
    return int(_tga_state().get("count") or 0)


def _tga_bump_today() -> None:
    with _tga_lock:
        st = _tga_state()
        st["count"] = int(st.get("count") or 0) + 1
        _tga_write_private(TGA_STATE_PATH, json.dumps(st).encode("utf-8"))


def _tga_rate(kind: str) -> None:
    limit, window = TGA_RATE[kind]
    now = time.time()
    with _tga_lock:
        arr = [t for t in _tga["attempts"][kind] if now - t < window]
        if len(arr) >= limit:
            wait = int(window - (now - arr[0])) + 1
            _tga["attempts"][kind] = arr
            raise HTTPException(status_code=429, detail=f"尝试次数过多，请 {max(1, wait // 60)} 分钟后再试")
        arr.append(now)
        _tga["attempts"][kind] = arr


def _tga_loop():
    with _tga_lock:
        if _tga["loop"] is not None and _tga["thread"] is not None and _tga["thread"].is_alive():
            return _tga["loop"]
        loop = asyncio.new_event_loop()

        def runner():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        th = threading.Thread(target=runner, name="tg-audio", daemon=True)
        th.start()
        _tga["loop"], _tga["thread"] = loop, th
        return loop


def _tga_run(coro, timeout: float = 60):
    fut = asyncio.run_coroutine_threadsafe(coro, _tga_loop())
    try:
        return fut.result(timeout)
    except concurrent.futures.TimeoutError:
        fut.cancel()
        raise HTTPException(status_code=504, detail="连接 Telegram 超时，请稍后再试")


def _tga_flood(exc) -> HTTPException:
    secs = int(getattr(exc, "seconds", 0) or 0)
    with _tga_lock:
        _tga["flood_until"] = max(_tga["flood_until"], time.time() + secs)
    return HTTPException(status_code=429, detail=f"Telegram 限流：需要等待 {secs} 秒后再操作")


def _tga_check_flood() -> None:
    left = int(_tga["flood_until"] - time.time())
    if left > 0:
        raise HTTPException(status_code=429, detail=f"Telegram 限流中，请 {left} 秒后再试")


async def _tga_client_async():
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    cfg = tga_cfg_load()
    if not tga_configured(cfg):
        raise HTTPException(status_code=409, detail="未配置 api_id / api_hash")
    key = (str(cfg.get("api_id")), str(cfg.get("api_hash")))
    cl = _tga["client"]
    if cl is not None and _tga["client_cfg"] != key:
        try:
            await cl.disconnect()
        except Exception:  # noqa: BLE001
            pass
        cl = None
    if cl is None:
        cl = TelegramClient(
            StringSession(tga_session_load() or None), int(cfg["api_id"]), str(cfg["api_hash"]),
            device_model="DJ666 Admin", system_version="Linux", app_version="dj666-1.0",
            lang_code="zh", system_lang_code="zh", receive_updates=False, flood_sleep_threshold=0,
            request_retries=2, connection_retries=2, retry_delay=2,
        )
        _tga["client"], _tga["client_cfg"] = cl, key
    if not cl.is_connected():
        await cl.connect()
    return cl


async def _tga_drop_client(log_out: bool = False) -> None:
    cl = _tga["client"]
    _tga["client"], _tga["client_cfg"] = None, None
    _tga["me"], _tga["authorized"], _tga["login"] = None, False, None
    _tga["entities"] = {}
    if cl is None:
        return
    try:
        if log_out and cl.is_connected():
            await cl.log_out()
    except Exception:  # noqa: BLE001
        pass
    try:
        await cl.disconnect()
    except Exception:  # noqa: BLE001
        pass


def _tga_me_public(me) -> Optional[dict]:
    if me is None:
        return None
    name = " ".join(x for x in [getattr(me, "first_name", "") or "", getattr(me, "last_name", "") or ""] if x).strip()
    return {"name": name or "Telegram 用户", "username": getattr(me, "username", None) or ""}


async def _tga_refresh_auth_async() -> bool:
    cl = await _tga_client_async()
    ok = await cl.is_user_authorized()
    _tga["authorized"] = bool(ok)
    _tga["me"] = _tga_me_public(await cl.get_me()) if ok else None
    _tga["checked"] = time.time()
    return bool(ok)


def tga_status(force: bool = False) -> dict:
    cfg = tga_cfg_load()
    configured = tga_configured(cfg)
    has_session = TGA_SESSION_PATH.is_file()
    err = ""
    if configured and has_session and (force or _tga["authorized"] is None or time.time() - _tga["checked"] > 600):
        try:
            _tga_run(_tga_refresh_auth_async(), 25)
        except HTTPException as exc:
            err = str(exc.detail)
        except Exception:  # noqa: BLE001
            err = "暂时连不上 Telegram，请稍后再试"
    login = _tga["login"]
    dl = _tga["download"]
    if dl and dl.get("job_id"):
        job = _opt_load(dl["job_id"])
        if job:
            dl = dict(dl, received=job.get("received") or 0, size=job.get("size") or dl.get("size"))
    flood = max(0, int(_tga["flood_until"] - time.time()))
    return {
        "configured": configured,
        "logged_in": bool(configured and has_session and _tga["authorized"]),
        "me": _tga["me"] if _tga["authorized"] else None,
        "login_stage": (login or {}).get("stage") if login and time.time() - login.get("ts", 0) < 900 else None,
        "daily_cap": tga_daily_cap(cfg),
        "today": _tga_count_today(),
        "flood_wait": flood,
        "download": dl,
        "error": err,
        "recent": tga_recent_public(),
        "batch": _tga_batch_public(),
    }


def _tga_admin(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


async def _tga_json(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    return data if isinstance(data, dict) else {}


def _tga_throttle() -> None:
    """Global ≥5s gap between list/search/resolve calls (waits out the remainder instead of failing)."""
    _tga_check_flood()
    with _tga_api_lock:
        gap = TGA_LIST_MIN_GAP - (time.time() - _tga["last_api"])
        if gap > 0:
            time.sleep(gap)
        _tga["last_api"] = time.time()


def _tga_need_login() -> None:
    if not tga_configured():
        raise HTTPException(status_code=409, detail="未配置 api_id / api_hash")
    if not TGA_SESSION_PATH.is_file() or not _tga["authorized"]:
        if TGA_SESSION_PATH.is_file() and _tga["authorized"] is None:
            tga_status(force=True)
            if _tga["authorized"]:
                return
        raise HTTPException(status_code=409, detail="请先登录 Telegram 账号")


@app.get("/admin/tg-audio/status")
def tga_status_ep(request: Request, refresh: int = 0, db: Session = Depends(get_db)):
    _tga_admin(request, db)
    return tga_status(force=bool(refresh))


@app.post("/admin/tg-audio/config")
async def tga_config_ep(request: Request, db: Session = Depends(get_db)):
    _opt_csrf(request)
    _tga_admin(request, db)
    data = await _tga_json(request)
    cfg = tga_cfg_load()
    if data.get("action") == "clear":
        await asyncio.to_thread(_tga_run, _tga_drop_client(True), 30)
        tga_session_delete()
        try:
            TGA_CFG_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": True, "message": "已清除 api_id / api_hash 并退出登录", **tga_status()}
    api_id = str(data.get("api_id") or "").strip()
    api_hash = str(data.get("api_hash") or "").strip()
    cap_raw = str(data.get("daily_cap") or "").strip()
    if api_id or api_hash:
        if not re.fullmatch(r"[0-9]{3,12}", api_id):
            raise HTTPException(status_code=400, detail="api_id 应为纯数字")
        if not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
            raise HTTPException(status_code=400, detail="api_hash 应为 32 位十六进制字符串")
        if (str(cfg.get("api_id")), str(cfg.get("api_hash"))) != (api_id, api_hash):
            await asyncio.to_thread(_tga_run, _tga_drop_client(False), 30)
        cfg["api_id"], cfg["api_hash"] = int(api_id), api_hash.lower()
    elif not tga_configured(cfg) and not cap_raw:
        raise HTTPException(status_code=400, detail="请填写 api_id 和 api_hash")
    if cap_raw:
        try:
            cap = int(cap_raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="每日下载上限应为数字")
        cfg["daily_cap"] = max(1, min(TGA_DAILY_MAX, cap))
    tga_cfg_save(cfg)
    return {"ok": True, "message": "已保存", **tga_status()}


@app.post("/admin/tg-audio/login/send-code")
async def tga_send_code_ep(request: Request, db: Session = Depends(get_db)):
    _opt_csrf(request)
    _tga_admin(request, db)
    data = await _tga_json(request)
    phone = re.sub(r"[\s\-()]", "", str(data.get("phone") or ""))
    if not TGA_PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="手机号格式不对，请带国家区号，如 +60123456789")
    if not tga_configured():
        raise HTTPException(status_code=409, detail="未配置 api_id / api_hash")
    _tga_check_flood()
    _tga_rate("send")

    async def go():
        from telethon import errors
        cl = await _tga_client_async()
        try:
            sent = await cl.send_code_request(phone)
        except errors.FloodWaitError as exc:
            raise _tga_flood(exc)
        except errors.PhoneNumberInvalidError:
            raise HTTPException(status_code=400, detail="手机号无效")
        except errors.PhoneNumberBannedError:
            raise HTTPException(status_code=400, detail="这个手机号已被 Telegram 封禁")
        except errors.ApiIdInvalidError:
            raise HTTPException(status_code=400, detail="api_id / api_hash 无效，请检查配置")
        except errors.RPCError as exc:
            raise HTTPException(status_code=400, detail=f"Telegram 拒绝发送验证码（{type(exc).__name__}）")
        _tga["login"] = {"phone": phone, "hash": sent.phone_code_hash, "stage": "code", "ts": time.time()}

    await asyncio.to_thread(_tga_run, go(), 40)
    return {"ok": True, "stage": "code", "message": "验证码已发送到你的 Telegram（或短信），请在 15 分钟内输入"}


@app.post("/admin/tg-audio/login/code")
async def tga_code_ep(request: Request, db: Session = Depends(get_db)):
    _opt_csrf(request)
    _tga_admin(request, db)
    data = await _tga_json(request)
    code = re.sub(r"\D", "", str(data.get("code") or ""))
    login = _tga["login"]
    if not login or login.get("stage") != "code" or time.time() - login.get("ts", 0) > 900:
        raise HTTPException(status_code=409, detail="请先发送验证码")
    if not 4 <= len(code) <= 8:
        raise HTTPException(status_code=400, detail="验证码格式不对")
    _tga_check_flood()
    _tga_rate("signin")

    async def go():
        from telethon import errors
        cl = await _tga_client_async()
        try:
            await cl.sign_in(phone=login["phone"], code=code, phone_code_hash=login["hash"])
        except errors.SessionPasswordNeededError:
            login["stage"] = "password"
            login["ts"] = time.time()
            return "password"
        except errors.FloodWaitError as exc:
            raise _tga_flood(exc)
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError):
            raise HTTPException(status_code=400, detail="验证码错误")
        except errors.PhoneCodeExpiredError:
            _tga["login"] = None
            raise HTTPException(status_code=400, detail="验证码已过期，请重新发送")
        except errors.RPCError as exc:
            raise HTTPException(status_code=400, detail=f"登录失败（{type(exc).__name__}）")
        return "ok"

    res = await asyncio.to_thread(_tga_run, go(), 40)
    if res == "password":
        return {"ok": True, "stage": "password", "message": "此账号开启了两步验证，请输入两步验证密码"}
    await asyncio.to_thread(_tga_finish_login)
    return {"ok": True, "stage": "done", "message": "登录成功", **tga_status()}


@app.post("/admin/tg-audio/login/password")
async def tga_password_ep(request: Request, db: Session = Depends(get_db)):
    _opt_csrf(request)
    _tga_admin(request, db)
    data = await _tga_json(request)
    pw = str(data.get("password") or "")
    login = _tga["login"]
    if not login or login.get("stage") != "password" or time.time() - login.get("ts", 0) > 900:
        raise HTTPException(status_code=409, detail="请先完成验证码步骤")
    if not pw or len(pw) > 256:
        raise HTTPException(status_code=400, detail="请输入两步验证密码")
    _tga_check_flood()
    _tga_rate("signin")

    async def go():
        from telethon import errors
        cl = await _tga_client_async()
        try:
            await cl.sign_in(password=pw)
        except errors.FloodWaitError as exc:
            raise _tga_flood(exc)
        except errors.PasswordHashInvalidError:
            raise HTTPException(status_code=400, detail="两步验证密码错误")
        except errors.RPCError as exc:
            raise HTTPException(status_code=400, detail=f"登录失败（{type(exc).__name__}）")

    await asyncio.to_thread(_tga_run, go(), 40)
    await asyncio.to_thread(_tga_finish_login)
    return {"ok": True, "stage": "done", "message": "登录成功", **tga_status()}


def _tga_finish_login() -> None:
    async def go():
        cl = await _tga_client_async()
        tga_session_save(cl.session.save())
        _tga["login"] = None
        await _tga_refresh_auth_async()

    _tga_run(go(), 30)


@app.post("/admin/tg-audio/logout")
async def tga_logout_ep(request: Request, db: Session = Depends(get_db)):
    _opt_csrf(request)
    _tga_admin(request, db)
    if TGA_SESSION_PATH.is_file() and tga_configured():
        try:
            await asyncio.to_thread(_tga_run, _tga_client_async(), 20)
        except Exception:  # noqa: BLE001
            pass
    await asyncio.to_thread(_tga_run, _tga_drop_client(True), 30)
    tga_session_delete()
    return {"ok": True, "message": "已退出登录并删除会话", **tga_status()}


def _tga_log(op: str, exc: BaseException) -> None:
    # Exception class only: never the phone, code, password, session or api values.
    print(f"[tg-audio] {op} failed: {type(exc).__name__}", flush=True)


def _tga_chat_ref(raw: str) -> str:
    """Normalise what the admin typed into one of: @username, +invitehash, id:<channel_id>."""
    raw = (raw or "").strip().strip("<>\"'“”").strip()
    raw = re.sub(r"\s+", "", raw)
    m = re.match(r"^tg://resolve\?domain=([A-Za-z0-9_]{4,64})", raw, re.I)
    if m:
        return "@" + m.group(1)
    m = re.match(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/(.*)$", raw, re.I)
    if m:
        path = re.split(r"[?#]", m.group(1), 1)[0].strip("/")
        parts = [p for p in path.split("/") if p]
        if parts and parts[0].lower() == "s":
            parts = parts[1:]
        if not parts:
            raise HTTPException(status_code=400, detail="链接里没有频道名，请输入完整链接，如 https://t.me/xxx")
        head = parts[0]
        if head.lower() == "c" and len(parts) >= 2 and parts[1].isdigit():
            return f"id:{int(parts[1])}"
        if head.lower() == "joinchat" and len(parts) >= 2:
            return "+" + parts[1]
        if head.startswith("+") and len(head) > 1:
            return head
        if re.fullmatch(r"[A-Za-z0-9_]{4,64}", head):
            return "@" + head
        raise HTTPException(status_code=400, detail="无法识别这个链接，请输入 https://t.me/频道名 或 @频道名")
    if raw.startswith("+") and re.fullmatch(r"\+[A-Za-z0-9_\-]{8,64}", raw):
        return raw
    if re.fullmatch(r"@?[A-Za-z0-9_]{4,64}", raw) and not raw.lstrip("@").isdigit():
        return "@" + raw.lstrip("@")
    if re.fullmatch(r"id:-?\d{3,20}", raw):
        n = int(raw[3:])
        if n < 0 and str(n).startswith("-100"):
            n = int(str(n)[4:])
        return f"id:{abs(n)}"
    raise HTTPException(status_code=400, detail="请输入频道链接（https://t.me/xxx）或 @用户名")


def _tga_looks_like_ref(raw: str) -> Optional[str]:
    raw = (raw or "").strip()
    if re.search(r"(t\.me|telegram\.me|telegram\.dog)/|^tg://|^@|^\+", raw, re.I):
        try:
            return _tga_chat_ref(raw)
        except HTTPException:
            return None
    return None


def _tga_chat_public(ent) -> dict:
    title = getattr(ent, "title", None) or " ".join(
        x for x in [getattr(ent, "first_name", "") or "", getattr(ent, "last_name", "") or ""] if x)
    uname = getattr(ent, "username", None) or ""
    if not uname and getattr(ent, "usernames", None):
        uname = next((u.username for u in ent.usernames if getattr(u, "active", True)), "") or ""
    if getattr(ent, "broadcast", False):
        kind = "频道"
    elif getattr(ent, "megagroup", False) or getattr(ent, "gigagroup", False):
        kind = "超级群组"
    elif hasattr(ent, "title"):
        kind = "群组"
    elif getattr(ent, "bot", False):
        kind = "机器人"
    else:
        kind = "用户"
    return {"key": f"id:{ent.id}", "title": title or uname or str(ent.id), "username": uname, "type": kind,
            "members": getattr(ent, "participants_count", None)}


async def _tga_entity(cl, ref: str):
    """Resolve a normalised ref to a channel / group entity, raising HTTPException with the real reason."""
    from telethon import errors
    from telethon.tl.functions.contacts import ResolveUsernameRequest
    from telethon.tl.functions.messages import CheckChatInviteRequest
    from telethon.tl.types import ChatInviteAlready, ChatInvitePeek, PeerChannel
    try:
        if ref.startswith("id:"):
            cid = int(ref[3:])
            ent = _tga["entities"].get(cid)
            if ent is None:
                try:
                    ah = tga_recent_access_hash(cid)
                    if ah is not None:
                        from telethon.tl.types import InputPeerChannel
                        ent = await cl.get_entity(InputPeerChannel(cid, ah))
                    else:
                        ent = await cl.get_entity(PeerChannel(cid))
                except ValueError:
                    raise HTTPException(status_code=404, detail="这个频道还没有缓存，请用链接 https://t.me/频道名 打开，或先搜索后点击结果（私有频道需要当前账号已加入）")
        elif ref.startswith("+"):
            inv = await cl(CheckChatInviteRequest(ref[1:]))
            if isinstance(inv, (ChatInviteAlready, ChatInvitePeek)):
                ent = inv.chat
            else:
                title = getattr(inv, "title", "") or "该频道"
                raise HTTPException(status_code=403, detail=f"这是邀请链接（{title}），当前账号还没有加入。请先在 Telegram 里用该账号加入后再打开；系统不会自动加入频道。")
        else:
            res = await cl(ResolveUsernameRequest(ref.lstrip("@")))
            ent = None
            peer = res.peer
            pid = getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None)
            if pid is not None:
                ent = next((c for c in res.chats if c.id == pid), None)
            if ent is None:
                u = next((x for x in res.users), None)
                if u is not None:
                    raise HTTPException(status_code=400, detail=f"@{ref.lstrip('@')} 是{'机器人' if getattr(u, 'bot', False) else '个人用户'}，不是频道或群组，无法读取音频")
                raise HTTPException(status_code=404, detail=f"找不到 @{ref.lstrip('@')}")
    except HTTPException:
        raise
    except errors.FloodWaitError as exc:
        raise _tga_flood(exc)
    except errors.UsernameNotOccupiedError:
        raise HTTPException(status_code=404, detail=f"用户名 {ref} 不存在（没有任何频道或用户使用这个用户名），请检查拼写")
    except errors.UsernameInvalidError:
        raise HTTPException(status_code=400, detail=f"用户名 {ref} 格式无效")
    except (errors.InviteHashExpiredError, errors.InviteHashInvalidError):
        raise HTTPException(status_code=404, detail="邀请链接已失效或无效")
    except (errors.ChannelPrivateError, errors.ChannelInvalidError):
        raise HTTPException(status_code=403, detail="这是私有频道，或当前账号被移出/封禁，无法读取")
    except errors.AuthKeyUnregisteredError as exc:
        _tga_log("resolve", exc)
        _tga["authorized"] = False
        raise HTTPException(status_code=401, detail="Telegram 登录已失效，请重新登录")
    except errors.RPCError as exc:
        _tga_log("resolve", exc)
        raise HTTPException(status_code=400, detail=f"打开频道失败（{type(exc).__name__}）")
    if getattr(ent, "left", False) and not getattr(ent, "username", None) and not getattr(ent, "broadcast", False):
        raise HTTPException(status_code=403, detail="当前账号不在这个私有群组里，无法读取")
    _tga["entities"][ent.id] = ent
    return ent


# --- 最近打开的频道 + 已下载/已导入 marks (data/tga_recent.json, data/tga_done.json; www-data 0600) ---
TGA_RECENT_PATH = DATA_DIR / "tga_recent.json"
TGA_DONE_PATH = DATA_DIR / "tga_done.json"
TGA_RECENT_KEEP = 20
TGA_DONE_KEEP = 5000


def _tga_json_load(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def tga_recent_load() -> list[dict]:
    return [e for e in _tga_json_load(TGA_RECENT_PATH, []) if isinstance(e, dict) and e.get("key")]


def _tga_recent_save(items: list[dict]) -> None:
    pinned = [e for e in items if e.get("pinned")]
    rest = [e for e in items if not e.get("pinned")]
    rest.sort(key=lambda e: float(e.get("opened") or 0), reverse=True)
    pinned.sort(key=lambda e: float(e.get("opened") or 0), reverse=True)
    _tga_write_private(TGA_RECENT_PATH, json.dumps(pinned + rest[:TGA_RECENT_KEEP], ensure_ascii=False).encode("utf-8"))


def tga_recent_public() -> list[dict]:
    out = []
    for e in tga_recent_load():
        ts = float(e.get("opened") or 0)
        out.append({
            "key": e["key"], "username": e.get("username") or "", "title": e.get("title") or "",
            "type": e.get("type") or "", "count": e.get("count"), "pinned": bool(e.get("pinned")),
            "opened": datetime.fromtimestamp(ts, timezone.utc).astimezone(TZ_CN).strftime("%m-%d %H:%M") if ts else "",
            "open": ("@" + e["username"]) if e.get("username") else e["key"],
        })
    pinned = [x for x in out if x["pinned"]]
    return pinned + [x for x in out if not x["pinned"]]


def tga_recent_record(ent, count) -> None:
    info = _tga_chat_public(ent)
    with _tga_lock:
        items = tga_recent_load()
        old = next((e for e in items if e["key"] == info["key"]), None)
        items = [e for e in items if e["key"] != info["key"]]
        entry = {
            "key": info["key"], "username": info["username"], "title": info["title"], "type": info["type"],
            "count": count if count is not None else (old or {}).get("count"), "opened": time.time(),
            "pinned": bool((old or {}).get("pinned")),
        }
        ah = getattr(ent, "access_hash", None)
        if ah is not None:
            entry["ah"] = int(ah)    # lets a private channel without @username reopen after a restart
        items.insert(0, entry)
        _tga_recent_save(items)


def tga_recent_access_hash(cid: int) -> Optional[int]:
    e = next((e for e in tga_recent_load() if e["key"] == f"id:{cid}"), None)
    try:
        return int(e["ah"]) if e and e.get("ah") is not None else None
    except (TypeError, ValueError):
        return None


def tga_done_mark(chat_id, msg_id, **fields) -> None:
    if not chat_id or not msg_id:
        return
    with _tga_lock:
        done = _tga_json_load(TGA_DONE_PATH, {})
        k = f"{int(chat_id)}:{int(msg_id)}"
        cur = done.get(k) if isinstance(done.get(k), dict) else {}
        cur.update(fields)
        cur["ts"] = time.time()
        done[k] = cur
        if len(done) > TGA_DONE_KEEP:
            for old in sorted(done, key=lambda x: float((done[x] or {}).get("ts") or 0))[: len(done) - TGA_DONE_KEEP]:
                done.pop(old, None)
        _tga_write_private(TGA_DONE_PATH, json.dumps(done).encode("utf-8"))


def _tga_id_aliases(cid) -> set:
    try:
        n = int(cid)
    except (TypeError, ValueError):
        return set()
    out = {n, abs(n)}
    s = str(abs(n))
    if s.startswith("100") and len(s) > 3:
        bare = int(s[3:])
        out.add(bare)
        out.add(-bare)
    else:
        out.add(int("100" + s))
        out.add(-int("100" + s))
    return {i for i in out if i}


def tga_done_map(chat_id) -> dict:
    prefixes = [f"{i}:" for i in _tga_id_aliases(chat_id)]
    out = {}
    for k, v in _tga_json_load(TGA_DONE_PATH, {}).items():
        if not isinstance(v, dict):
            continue
        for pre in prefixes:
            if k.startswith(pre):
                try:
                    out[int(k[len(pre):])] = v
                except ValueError:
                    pass
                break
    return out


def tga_sync_marks_from_jobs() -> None:
    """Backfill tga_done.json (and missing job tg_chat_id) from optimize jobs already imported or downloaded."""
    done = _tga_json_load(TGA_DONE_PATH, {})
    job_to_key = {}
    for k, v in done.items():
        if isinstance(v, dict) and v.get("job"):
            job_to_key[str(v["job"])] = k
    for job in _opt_all_jobs():
        if job.get("source") != "telegram":
            continue
        chat_id, msg_id = job.get("tg_chat_id"), job.get("tg_msg")
        if (not chat_id or not msg_id) and job.get("id") in job_to_key:
            parts = str(job_to_key[job["id"]]).split(":")
            if len(parts) == 2:
                try:
                    chat_id = chat_id or int(parts[0])
                    msg_id = msg_id or int(parts[1])
                except ValueError:
                    pass
                if chat_id and not job.get("tg_chat_id"):
                    _opt_update(job["id"], tg_chat_id=int(chat_id))
        if not chat_id or not msg_id:
            continue
        fields = {"downloaded": True, "job": job["id"]}
        if job.get("track_id"):
            fields["track_id"] = job["track_id"]
        try:
            tga_done_mark(chat_id, msg_id, **fields)
        except Exception:  # noqa: BLE001
            pass


@app.get("/admin/tg-audio/recent")
def tga_recent_ep(request: Request, db: Session = Depends(get_db)):
    _tga_admin(request, db)
    return {"recent": tga_recent_public()}


@app.post("/admin/tg-audio/recent")
async def tga_recent_edit_ep(request: Request, db: Session = Depends(get_db)):
    _opt_csrf(request)
    _tga_admin(request, db)
    data = await _tga_json(request)
    key = str(data.get("key") or "")
    action = str(data.get("action") or "")
    if not re.fullmatch(r"id:\d{1,20}", key) or action not in ("remove", "pin", "unpin"):
        raise HTTPException(status_code=400, detail="参数错误")
    with _tga_lock:
        items = tga_recent_load()
        if not any(e["key"] == key for e in items):
            raise HTTPException(status_code=404, detail="记录不存在")
        if action == "remove":
            items = [e for e in items if e["key"] != key]
        else:
            for e in items:
                if e["key"] == key:
                    e["pinned"] = action == "pin"
        _tga_recent_save(items)
    return {"ok": True, "recent": tga_recent_public()}


def _tga_audio_item(msg) -> Optional[dict]:
    f = getattr(msg, "file", None)
    if f is None or getattr(msg, "document", None) is None:
        return None
    mime = (f.mime_type or "").lower()
    name = f.name or ""
    ext = (Path(name).suffix.lower() if name else "") or (f.ext or "").lower()
    if ext == ".oga":
        ext = ".ogg"
    if not mime.startswith("audio/") and ext not in OPT_EXTS:
        return None
    title = f.title or (Path(name).stem if name else "") or f"音频 {msg.id}"
    d = msg.date.astimezone(TZ_CN).strftime("%Y-%m-%d %H:%M") if msg.date else ""
    return {
        "id": msg.id, "title": title, "performer": f.performer or "", "duration": f.duration or 0,
        "size": f.size or 0, "date": d, "name": name, "ext": ext, "mime": mime,
        "ok": ext in OPT_EXTS and 0 < (f.size or 0) <= OPT_MAX_BYTES_ADMIN,
    }


@app.post("/admin/tg-audio/search")
async def tga_search_ep(request: Request, db: Session = Depends(get_db)):
    _opt_csrf(request)
    _tga_admin(request, db)
    data = await _tga_json(request)
    q = str(data.get("q") or "").strip()[:200]
    direct = _tga_looks_like_ref(q)
    if not direct and len(q) < 2:
        raise HTTPException(status_code=400, detail="搜索词至少 2 个字")
    await asyncio.to_thread(_tga_need_login)
    await asyncio.to_thread(_tga_throttle)

    async def go():
        from telethon import errors
        from telethon.tl.functions.contacts import SearchRequest
        cl = await _tga_client_async()
        out, seen = [], set()
        # A pasted link / @username is resolved exactly first (global search is fuzzy and can miss it).
        uname = direct.lstrip("@") if direct and direct.startswith("@") else (q if re.fullmatch(r"[A-Za-z0-9_]{4,64}", q) else None)
        exact_err = ""
        if direct and not direct.startswith("@"):
            try:
                ent = await _tga_entity(cl, direct)
                out.append(_tga_chat_public(ent)); seen.add(ent.id)
            except HTTPException as exc:
                exact_err = str(exc.detail)
            return out, exact_err
        if uname:
            try:
                ent = await _tga_entity(cl, "@" + uname)
                out.append(_tga_chat_public(ent)); seen.add(ent.id)
            except HTTPException as exc:
                exact_err = str(exc.detail)
        try:
            res = await cl(SearchRequest(q=uname or q, limit=20))
        except errors.FloodWaitError as exc:
            raise _tga_flood(exc)
        except errors.RPCError as exc:
            _tga_log("search", exc)
            if out:
                return out, ""
            raise HTTPException(status_code=400, detail=f"搜索失败（{type(exc).__name__}）")
        for ch in res.chats:
            if ch.id in seen or not hasattr(ch, "title"):
                continue
            _tga["entities"][ch.id] = ch
            seen.add(ch.id)
            out.append(_tga_chat_public(ch))
        return out, ("" if out else exact_err)

    results, err = await asyncio.to_thread(_tga_run, go(), 50)
    return {"results": results, "error": err}


@app.post("/admin/tg-audio/list")
async def tga_list_ep(request: Request, db: Session = Depends(get_db)):
    _opt_csrf(request)
    _tga_admin(request, db)
    data = await _tga_json(request)
    ref = _tga_chat_ref(str(data.get("chat") or ""))
    try:
        offset_id = max(0, int(data.get("offset_id") or 0))
    except (TypeError, ValueError):
        offset_id = 0
    await asyncio.to_thread(_tga_need_login)
    await asyncio.to_thread(_tga_throttle)

    async def go():
        from telethon import errors
        from telethon.tl.types import InputMessagesFilterMusic
        cl = await _tga_client_async()
        ent = await _tga_entity(cl, ref)
        try:
            msgs = await cl.get_messages(ent, limit=TGA_PAGE, offset_id=offset_id, filter=InputMessagesFilterMusic)
        except errors.FloodWaitError as exc:
            raise _tga_flood(exc)
        except (errors.ChannelPrivateError, errors.ChatAdminRequiredError):
            raise HTTPException(status_code=403, detail="该账号无权读取这个频道（私有频道需先加入）")
        except errors.RPCError as exc:
            _tga_log("list", exc)
            raise HTTPException(status_code=400, detail=f"读取失败（{type(exc).__name__}）")
        items = [x for x in (_tga_audio_item(m) for m in msgs) if x]
        try:
            tga_sync_marks_from_jobs()
        except Exception as exc:  # noqa: BLE001
            _tga_log("sync-marks", exc)
        done = tga_done_map(ent.id)
        aliases = _tga_id_aliases(ent.id)
        for job in _opt_all_jobs():
            if job.get("source") != "telegram":
                continue
            try:
                jcid = int(job.get("tg_chat_id") or 0)
            except (TypeError, ValueError):
                jcid = 0
            if jcid not in aliases:
                continue
            try:
                mid = int(job.get("tg_msg") or 0)
            except (TypeError, ValueError):
                mid = 0
            if not mid:
                continue
            cur = done.get(mid) or {}
            cur["downloaded"] = True
            if job.get("track_id"):
                cur["track_id"] = job["track_id"]
            done[mid] = cur
        for it in items:
            d0 = done.get(it["id"]) or {}
            it["downloaded"] = bool(d0.get("downloaded") or d0.get("track_id"))
            it["imported"] = bool(d0.get("track_id"))
        last = msgs[-1].id if msgs else 0
        total = getattr(msgs, "total", None)
        try:
            tga_recent_record(ent, total if offset_id == 0 else None)
        except Exception as exc:  # noqa: BLE001
            _tga_log("recent", exc)
        return {
            "recent": tga_recent_public(),
            "chat": _tga_chat_public(ent), "items": items,
            "next_offset": last if len(msgs) >= TGA_PAGE else None,
            "total": getattr(msgs, "total", None),
        }

    return await asyncio.to_thread(_tga_run, go(), 60)


async def _tga_download_async(job_id: str, ref: str, msg_id: int) -> None:
    from telethon import errors
    d = _opt_job_dir(job_id)
    dl = _tga["download"]
    try:
        cl = await _tga_client_async()
        ent = await _tga_entity(cl, ref)
        msg = await cl.get_messages(ent, ids=msg_id)
        if msg is None or getattr(msg, "document", None) is None:
            raise RuntimeError("消息不存在或不是音频文件")
        job = _opt_load(job_id) or {}
        path = d / f"orig{job.get('ext') or '.mp3'}"
        total = int(job.get("size") or 0)
        got = 0
        last_up = 0.0
        while True:
            try:
                with path.open("ab") as fh:
                    async for chunk in cl.iter_download(msg.document, offset=got, request_size=512 * 1024):
                        if not d.is_dir() or (_opt_load(job_id) or {}).get("state") != "downloading":
                            raise RuntimeError("cancelled")
                        got += len(chunk)
                        if got > OPT_MAX_BYTES_ADMIN or (total and got > total):
                            raise RuntimeError("文件大小超出限制")
                        fh.write(chunk)
                        if time.time() - last_up > 1:
                            last_up = time.time()
                            _opt_update(job_id, received=got, stage="从 Telegram 下载中")
                            dl["received"] = got
                break
            except errors.FloodWaitError as exc:
                secs = int(exc.seconds or 0)
                _tga["flood_until"] = time.time() + secs
                if secs > TGA_FLOOD_AUTO_WAIT:
                    raise RuntimeError(f"Telegram 限流：需等待 {secs} 秒，请稍后重新下载")
                _opt_update(job_id, stage=f"Telegram 限流，{secs} 秒后自动继续")
                dl["state"] = f"限流等待 {secs} 秒"
                await asyncio.sleep(secs + 1)
                dl["state"] = "downloading"
        if total and got != total:
            raise RuntimeError("下载不完整，请重试")
        dl["received"] = got
        job = _opt_update(job_id, received=got, size=got, state="queued", stage="排队中", progress=0, error=None)
        if job:
            _opt_queue.put(job_id)
        dl["state"] = "done"
        try:
            j2 = _opt_load(job_id) or {}
            tga_done_mark(j2.get("tg_chat_id"), j2.get("tg_msg"), downloaded=True, job=job_id)
        except Exception as exc:  # noqa: BLE001
            _tga_log("mark", exc)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, HTTPException):
            msg_txt = str(exc.detail)
        elif isinstance(exc, RuntimeError):
            msg_txt = str(exc)
        else:
            _tga_log("download", exc)
            msg_txt = f"下载失败（{type(exc).__name__}）"
        if msg_txt == "cancelled":
            dl["state"] = "cancelled"
        else:
            dl["state"], dl["error"] = "failed", msg_txt
            _opt_update(job_id, state="failed", stage="失败", error=msg_txt[:300], finished=time.time())
            try:
                for p in d.glob("orig*"):
                    p.unlink(missing_ok=True)
            except OSError:
                pass
    finally:
        dl["active"] = False


@app.post("/admin/tg-audio/download")
async def tga_download_ep(request: Request, db: Session = Depends(get_db)):
    _opt_csrf(request)
    user = _tga_admin(request, db)
    data = await _tga_json(request)
    ref = _tga_chat_ref(str(data.get("chat") or ""))
    try:
        msg_id = int(data.get("msg_id") or 0)
    except (TypeError, ValueError):
        msg_id = 0
    if msg_id <= 0:
        raise HTTPException(status_code=400, detail="缺少消息 ID")
    target = str(data.get("target") or OPT_DEFAULT_TARGET)
    if target not in OPT_TARGETS:
        target = OPT_DEFAULT_TARGET
    await asyncio.to_thread(_tga_need_login)
    _tga_check_flood()
    with _tga_lock:
        if _tga_batch_busy():
            raise HTTPException(status_code=429, detail="批量下载进行中，请等它完成或先取消剩余排队")
        cur = _tga["download"]
        if cur and cur.get("active"):
            raise HTTPException(status_code=429, detail="已有一个下载在进行中，请等它完成（全站一次只下载一个）")
        cap = tga_daily_cap()
        if _tga_count_today() >= cap:
            raise HTTPException(status_code=429, detail=f"今天已下载 {cap} 个，达到每日上限（可在配置里调整）")
        _tga["download"] = {"active": True, "job_id": None, "name": "", "size": 0, "received": 0, "state": "preparing", "error": ""}
    try:
        await asyncio.to_thread(_tga_throttle)
        job_id, name, _sz = await asyncio.to_thread(_tga_run, _tga_prepare_job(user.id, ref, msg_id, target), 40)
        asyncio.run_coroutine_threadsafe(_tga_download_async(job_id, ref, msg_id), _tga_loop())
    except BaseException:
        if _tga["download"] and not _tga["download"].get("job_id"):
            _tga["download"]["active"] = False
            _tga["download"]["state"] = "idle"
        raise
    return {"ok": True, "job_id": job_id, "name": name, **tga_status()}


def _tga_batch_empty() -> dict:
    return {"items": [], "active": False, "cancel": False, "message": "", "chat": "", "chat_title": ""}


def _tga_batch_persist() -> None:
    data = _tga.get("batch") or _tga_batch_empty()
    try:
        _tga_write_private(TGA_BATCH_PATH, json.dumps(data, ensure_ascii=False).encode("utf-8"))
    except OSError:
        pass


def _tga_batch_busy() -> bool:
    b = _tga.get("batch") or {}
    if b.get("active"):
        return True
    return any((it or {}).get("status") in ("queued", "downloading") for it in (b.get("items") or []))


def _tga_batch_public() -> dict:
    with _tga_lock:
        raw = _tga.get("batch") or _tga_batch_empty()
        items = [dict(it) for it in (raw.get("items") or []) if isinstance(it, dict)]
        out = {
            "items": items,
            "active": bool(raw.get("active")),
            "cancel": bool(raw.get("cancel")),
            "message": raw.get("message") or "",
            "chat": raw.get("chat") or "",
            "chat_title": raw.get("chat_title") or "",
        }
    for it in out["items"]:
        jid = it.get("job_id")
        st = it.get("status")
        if not jid or st in ("cancelled", "skipped"):
            continue
        job = _opt_load(str(jid))
        if not job:
            continue
        it["received"] = job.get("received") or it.get("received") or 0
        it["size"] = job.get("size") or it.get("size") or 0
        it["name"] = job.get("name") or it.get("name") or ""
        js = job.get("state")
        if js == "downloading":
            it["status"] = "downloading"
            it["progress"] = int(round(100 * (it["received"] or 0) / it["size"])) if it.get("size") else 0
        elif js in ("queued", "running"):
            it["status"] = "optimizing"
            it["progress"] = int(job.get("progress") or 0)
        elif js == "done":
            it["status"] = "done"
            it["progress"] = 100
        elif js == "failed" and st != "cancelled":
            it["status"] = "failed"
            it["error"] = job.get("error") or it.get("error") or "失败"
        if it["status"] == "downloading" and it.get("size"):
            it["progress"] = int(round(100 * (it["received"] or 0) / it["size"]))
    out["active"] = bool(out["active"] or any(
        it.get("status") in ("queued", "downloading") for it in out["items"]))
    return out


async def _tga_throttle_async(wait_flood: bool = False) -> None:
    """Async counterpart of _tga_throttle; safe to await on the Telethon loop."""
    if wait_flood:
        left = int(_tga["flood_until"] - time.time())
        if left > 0:
            await asyncio.sleep(left + 1)
    else:
        _tga_check_flood()
    while True:
        with _tga_api_lock:
            gap = TGA_LIST_MIN_GAP - (time.time() - _tga["last_api"])
            if gap <= 0:
                _tga["last_api"] = time.time()
                return
        await asyncio.sleep(gap)


async def _tga_prepare_job(user_id: int, ref: str, msg_id: int, target: str) -> tuple:
    """Create the optimize job and fill the download slot. Must run on the Telethon loop."""
    from telethon import errors
    cl = await _tga_client_async()
    try:
        ent = await _tga_entity(cl, ref)
        msg = await cl.get_messages(ent, ids=msg_id)
    except errors.FloodWaitError as exc:
        raise _tga_flood(exc)
    except errors.RPCError as exc:
        raise HTTPException(status_code=400, detail=f"读取消息失败（{type(exc).__name__}）")
    item = _tga_audio_item(msg) if msg is not None else None
    if not item:
        raise HTTPException(status_code=404, detail="消息不存在或不是音频文件")
    if item["ext"] not in OPT_EXTS:
        raise HTTPException(status_code=400, detail="不支持的音频格式")
    if not 0 < item["size"] <= OPT_MAX_BYTES_ADMIN:
        raise HTTPException(status_code=413, detail=f"文件超过 {OPT_MAX_BYTES_ADMIN // (1024 * 1024)}MB 上限")
    _opt_require_disk(_opt_est_need(src_size=item["size"], src_on_disk=False), http=True)
    chat = _tga_chat_public(ent)
    base = " - ".join(x for x in [item["performer"], item["title"]] if x) or item["name"] or f"telegram-{msg_id}"
    name = _opt_name_clean(Path(base).stem + item["ext"])
    job_id = secrets.token_hex(12)
    d = _opt_job_dir(job_id)
    d.mkdir(parents=True, exist_ok=False)
    (d / f"orig{item['ext']}").touch()
    job = {
        "id": job_id, "user_id": user_id, "is_admin": True, "name": name, "ext": item["ext"],
        "source": "telegram", "size": item["size"], "received": 0, "state": "downloading",
        "stage": "从 Telegram 下载中", "progress": 0, "target": target, "created": time.time(),
        "tg_chat": chat.get("title"), "tg_msg": msg_id, "tg_chat_id": int(chat["key"][3:]),
    }
    with _opt_lock:
        _opt_save(job)
    _tga_bump_today()
    with _tga_lock:
        if _tga["download"]:
            _tga["download"].update(job_id=job_id, name=name, size=item["size"], state="downloading")
    return job_id, name, item["size"]


async def _tga_batch_worker(user_id: int, ref: str, target: str) -> None:
    """Process the persisted queue sequentially on the Telethon loop (do not call _tga_run here)."""
    try:
        while True:
            with _tga_lock:
                batch = _tga.get("batch") or _tga_batch_empty()
                if batch.get("cancel"):
                    for it in batch.get("items") or []:
                        if (it or {}).get("status") == "queued":
                            it["status"] = "cancelled"
                            it["error"] = "已取消"
                    batch["active"] = False
                    batch["cancel"] = False
                    _tga["batch"] = batch
                    _tga_batch_persist()
                    return
                nxt = next((it for it in (batch.get("items") or []) if (it or {}).get("status") == "queued"), None)
                if not nxt:
                    batch["active"] = False
                    _tga["batch"] = batch
                    _tga_batch_persist()
                    return
                nxt["status"] = "downloading"
                nxt["error"] = ""
                nxt["progress"] = 0
                _tga["batch"] = batch
                _tga_batch_persist()
                msg_id = int(nxt["msg_id"])
            while True:
                with _tga_lock:
                    cur = _tga.get("download")
                    if not (cur and cur.get("active")):
                        _tga["download"] = {
                            "active": True, "job_id": None, "name": nxt.get("title") or nxt.get("name") or "",
                            "size": 0, "received": 0, "state": "preparing", "error": "",
                        }
                        break
                await asyncio.sleep(0.4)
            cap = tga_daily_cap()
            if _tga_count_today() >= cap:
                with _tga_lock:
                    nxt["status"] = "failed"
                    nxt["error"] = f"今天已下载 {cap} 个，达到每日上限"
                    if _tga["download"] and not _tga["download"].get("job_id"):
                        _tga["download"]["active"] = False
                        _tga["download"]["state"] = "idle"
                    _tga_batch_persist()
                continue
            try:
                await _tga_throttle_async(wait_flood=True)
                job_id, name, size = await _tga_prepare_job(user_id, ref, msg_id, target)
                with _tga_lock:
                    nxt["job_id"] = job_id
                    nxt["name"] = name
                    nxt["size"] = size
                    _tga_batch_persist()
                await _tga_download_async(job_id, ref, msg_id)
                job = _opt_load(job_id) or {}
                with _tga_lock:
                    if job.get("state") in ("queued", "running"):
                        nxt["status"] = "optimizing"
                        nxt["error"] = ""
                    elif job.get("state") == "done":
                        nxt["status"] = "done"
                        nxt["progress"] = 100
                    else:
                        nxt["status"] = "failed"
                        nxt["error"] = job.get("error") or ((_tga.get("download") or {}).get("error")) or "下载失败"
                    _tga_batch_persist()
            except Exception as exc:  # noqa: BLE001
                with _tga_lock:
                    if _tga["download"] and not _tga["download"].get("job_id"):
                        _tga["download"]["active"] = False
                        _tga["download"]["state"] = "idle"
                    if isinstance(exc, HTTPException):
                        msg_txt = str(exc.detail)
                    else:
                        _tga_log("batch", exc)
                        msg_txt = f"下载失败（{type(exc).__name__}）"
                    nxt["status"] = "failed"
                    nxt["error"] = (msg_txt or "下载失败")[:300]
                    _tga_batch_persist()
    except Exception as exc:  # noqa: BLE001
        _tga_log("batch-worker", exc)
        with _tga_lock:
            batch = _tga.get("batch") or _tga_batch_empty()
            for it in batch.get("items") or []:
                if (it or {}).get("status") in ("queued", "downloading"):
                    it["status"] = "failed"
                    it["error"] = it.get("error") or "批量任务中断"
            batch["active"] = False
            _tga["batch"] = batch
            _tga_batch_persist()


@app.post("/admin/tg-audio/batch")
async def tga_batch_ep(request: Request, db: Session = Depends(get_db)):
    _opt_csrf(request)
    user = _tga_admin(request, db)
    data = await _tga_json(request)
    ref = _tga_chat_ref(str(data.get("chat") or ""))
    target = str(data.get("target") or OPT_DEFAULT_TARGET)
    if target not in OPT_TARGETS:
        target = OPT_DEFAULT_TARGET
    raw_items = data.get("items") if isinstance(data.get("items"), list) else []
    parsed = []
    seen = set()
    for raw in raw_items[:100]:
        if not isinstance(raw, dict):
            continue
        try:
            mid = int(raw.get("msg_id") or 0)
        except (TypeError, ValueError):
            mid = 0
        if mid <= 0 or mid in seen:
            continue
        seen.add(mid)
        parsed.append({
            "msg_id": mid,
            "force": bool(raw.get("force")),
            "title": str(raw.get("title") or "")[:200],
        })
    if not parsed:
        raise HTTPException(status_code=400, detail="请先勾选要下载的音频")
    await asyncio.to_thread(_tga_need_login)
    _tga_check_flood()
    with _tga_lock:
        if _tga_batch_busy():
            raise HTTPException(status_code=429, detail="已有批量任务在进行中，请等它完成或先取消剩余排队")
        cur = _tga["download"]
        if cur and cur.get("active"):
            raise HTTPException(status_code=429, detail="已有一个下载在进行中，请等它完成后再开始批量")

    def _marks():
        try:
            tga_sync_marks_from_jobs()
        except Exception as exc:  # noqa: BLE001
            _tga_log("sync-marks", exc)
        chat_id = None
        if ref.startswith("id:"):
            try:
                chat_id = int(ref[3:])
            except ValueError:
                chat_id = None
        if chat_id is None:
            async def resolve():
                cl = await _tga_client_async()
                ent = await _tga_entity(cl, ref)
                return int(ent.id)
            _tga_throttle()
            chat_id = _tga_run(resolve(), 40)
        return tga_done_map(chat_id), chat_id

    done_map, chat_id = await asyncio.to_thread(_marks)
    skipped, queued, capped_n = [], [], 0
    remain = max(0, tga_daily_cap() - _tga_count_today())
    if remain <= 0:
        raise HTTPException(status_code=429, detail=f"今天已下载 {tga_daily_cap()} 个，达到每日上限（可在配置里调整）")
    for it in parsed:
        mark = done_map.get(it["msg_id"]) or {}
        already = bool(mark.get("downloaded") or mark.get("track_id"))
        if already and not it["force"]:
            skipped.append(it)
            continue
        if len(queued) >= remain:
            capped_n += 1
            continue
        queued.append({
            "msg_id": it["msg_id"],
            "title": it["title"],
            "name": it["title"],
            "force": it["force"],
            "job_id": None,
            "size": 0,
            "received": 0,
            "progress": 0,
            "status": "queued",
            "error": "",
            "chat_id": chat_id,
        })
    parts = []
    if queued:
        parts.append(f"已加入 {len(queued)} 首到下载队列")
    if skipped:
        parts.append(f"跳过 {len(skipped)} 首已下载/已导入（如需重新下载请单独勾选）")
    if capped_n:
        parts.append(f"另有 {capped_n} 首因今日额度已满未加入（今日剩余 {remain} / {tga_daily_cap()}）")
    message = "；".join(parts) + "。" if parts else ""
    if not queued:
        raise HTTPException(status_code=400, detail=message or "没有可加入队列的条目")
    batch = {
        "items": queued,
        "active": True,
        "cancel": False,
        "message": message,
        "chat": ref,
        "chat_title": "",
        "chat_id": chat_id,
    }
    with _tga_lock:
        if _tga_batch_busy() or (_tga["download"] and _tga["download"].get("active")):
            raise HTTPException(status_code=429, detail="已有下载在进行中，请稍后再试")
        _tga["batch"] = batch
        _tga_batch_persist()
    asyncio.run_coroutine_threadsafe(_tga_batch_worker(user.id, ref, target), _tga_loop())
    return {
        "ok": True,
        "queued": len(queued),
        "skipped": len(skipped),
        "capped": capped_n,
        "message": message,
        **tga_status(),
    }


@app.post("/admin/tg-audio/batch/cancel")
async def tga_batch_cancel_ep(request: Request, db: Session = Depends(get_db)):
    _opt_csrf(request)
    _tga_admin(request, db)
    n = 0
    with _tga_lock:
        batch = _tga.get("batch") or _tga_batch_empty()
        for it in batch.get("items") or []:
            if (it or {}).get("status") == "queued":
                it["status"] = "cancelled"
                it["error"] = "已取消"
                n += 1
        batch["cancel"] = True
        if n == 0 and not any((it or {}).get("status") == "downloading" for it in (batch.get("items") or [])):
            batch["active"] = False
            batch["cancel"] = False
        batch["message"] = f"已取消 {n} 首排队中的条目" + ("（当前正在下载的会继续完成）" if any(
            (it or {}).get("status") == "downloading" for it in (batch.get("items") or [])) else "")
        _tga["batch"] = batch
        _tga_batch_persist()
    return {"ok": True, "cancelled": n, "message": batch["message"], **tga_status()}


def tga_start() -> None:
    """After a restart, any Telegram download that was in flight is marked failed (re-download to retry)."""
    for job in _opt_all_jobs():
        if job.get("state") == "downloading":
            _opt_update(job["id"], state="failed", stage="失败", error="服务重启，Telegram 下载中断，请重新下载",
                        finished=time.time())
    try:
        raw = json.loads(TGA_BATCH_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if isinstance(raw, dict) and raw.get("items"):
        for it in raw.get("items") or []:
            if isinstance(it, dict) and it.get("status") in ("queued", "downloading"):
                it["status"] = "failed"
                it["error"] = "服务重启，批量任务中断，请重新排队"
        raw["active"] = False
        raw["cancel"] = False
        _tga["batch"] = raw
        _tga_batch_persist()
    try:
        tga_sync_marks_from_jobs()
    except Exception:  # noqa: BLE001
        pass


@app.get("/uploads/{filename}")
def serve_upload(filename: str, request: Request, db: Session = Depends(get_db)):
    safe = Path(filename).name
    if safe != filename or not safe:
        raise HTTPException(status_code=404, detail="文件不存在")
    path = (UPLOAD_DIR / safe).resolve()
    if path.parent != UPLOAD_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    track = db.scalar(select(Track).where(Track.filename == safe))
    if not track:
        raise HTTPException(status_code=404, detail="文件不存在")
    if track.status != TRACK_STATUS_APPROVED:
        user = current_user(request, db)
        if not user or (not user.is_admin and user.id != track.uploader_id):
            raise HTTPException(status_code=404, detail="文件不存在")
    media = {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
        ".m4a": "audio/mp4", ".flac": "audio/flac",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, filename=track.original_name or safe)


@app.get("/health")
def health():
    return {"ok": True, "app": "dj666"}
