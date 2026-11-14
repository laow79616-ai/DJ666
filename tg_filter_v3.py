"""Stronger Telegram ad filters (v3): haystack, categories, OCR, user-pool hard blocks.

Imported by main.py. Does not import main (avoids circular imports).
"""
from __future__ import annotations

import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Normalization (kept in sync with main.tg_norm)
# ---------------------------------------------------------------------------

def tg_norm(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(ch for ch in folded if ch.isalnum() or ch in "@#")


NEWS_RE = re.compile(
    r"警方|被捕|窝点|电诈|诈骗|检察院|法院|报道|资讯|受害者|查处|扫荡|打击|"
    r"涉嫌|逮捕|通缉|调查组|拘留|扣押|#曝光|#群友爆料|#群友反馈|#群友投稿|"
    r"#网友投稿|#避雷|杀猪盘|跑路|被黑|维权|黑客|被盗|遭窃|遭黑客|"
    r"案件|新闻|最新消息|捣毁|查获|查扣|抓获|扫黄|查封|停业整顿|突袭|"
    r"拒不付款|抢走|爆炸|遇难|火灾|公开曝光|提高警惕|"
    r"洗钱|制裁|监管|使馆|紧急提醒|供应链"
)

# Advertising call-to-action: news exemption is cancelled when these appear.
CTA_RE = re.compile(
    r"返水|起步返水|首充|日出千万|点击上桌|一键试投|点击加入游戏|"
    r"注册即送|代理加盟|诚招代理|打码量|洗码|娱乐群组|自动上下分|"
    r"哈希娱乐|链上u|usdt充提|官方直营|"
    r"#友情广告|#付费广告|#此次活动由|"
    r"独家赞助|老牌赞助商|赞助\s*\d+|"
    r"选妃|真照上门|上门服务|私密大型会所|震撼现场海选|"
    r"在线客服|咨询电话|营业时间|"
    r"爆庄网址|活动频道|无需注册|关注领|"
    r"注册登入|点击注册"
)

AD_TAG_RE = re.compile(r"#(?:友情广告|付费广告|此次活动由)")

SPONSOR_RE = re.compile(
    r"#此次活动由|独家赞助|老牌赞助商|"
    r"此次活动由.{0,30}赞助|"
    r"赞助\s*\d+\s*(?:万|w|rmb|人民币)",
    re.I,
)

# Strong lottery / draw-result patterns (graphic captions or OCR).
LOTTERY_HARD_RE = re.compile(
    r"开奖结果|开出了\s*\d+|特码|六合彩|香港彩|快三|时时彩|"
    r"大乐透|双色球|快乐8|排列[35]|3d试机号|"
    r"pk\s*10|pk10|极速号码|号码轮盘|"
    r"第\s*\d{3,}\s*期|期号\s*[:：]?\s*\d+|"
    r"竟然又开出",
    re.I,
)
# Sequences like 01 08 15 23 31 + 特/球, or 4-digit draw "9991".
LOTTERY_BALLS_RE = re.compile(
    r"(?:(?:[0-2]?\d)[\s,，、.-]+){5,}(?:[0-3]?\d)"
)

VENUE_STRONG_RE = re.compile(
    r"选妃|真照上门|上门服务|私密大型会所|私密会所|"
    r"震撼现场海选|会所营业时间|会所.{0,8}营业|"
    r"妹子都很润|在线选妃"
)
VENUE_WORD_RE = re.compile(r"会所|按摩|spa|ktv|宵夜|陪玩|上门", re.I)

PHONE_RE = re.compile(
    r"(?:\+?6\s*0|0)[\s-]?\d{2,3}[\s-]?\d{3,4}[\s-]?\d{3,4}"
    r"|(?:\+?\d{1,3}[\s-]?)?\d{8,14}"
)
HANDLE_RE = re.compile(r"@[A-Za-z0-9_]{3,}")
TME_RE = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/[A-Za-z0-9_]+", re.I)
WAME_RE = re.compile(r"(?:https?://)?(?:wa\.me|api\.whatsapp\.com)/\S+", re.I)

# Domains / handles learned from offending posts (normalized hay contains these).
BLOCK_DOMAINS = (
    "y3666.com", "y3666com", "122.vip", "122vip",
    "x6.com",  # earlier ignored #付费广告
)
# Use dotted forms in raw text too.
BLOCK_DOMAIN_RE = re.compile(
    r"(?i)(?:y3666\.com|122\.vip|\bx6\.com\b|\b78\.com\b)",
)
BLOCK_HANDLES = {
    "y3y3", "y3_69bot", "pg321", "zsj44", "zsj444",
    "tg78", "df78", "kf78com", "dl78com", "kf78",
}

SERVICE_WORDS_RE = re.compile(
    r"客服|咨询|注册|官网|上分|选妃|报名|加盟|代理|充值|开户|领取"
)

OCR_MAX_BYTES = 8 * 1024 * 1024
OCR_TIMEOUT = 8
OCR_MAX_IMAGES = 2
OCR_LANGS = "chi_sim+eng"


def is_news(text: str) -> bool:
    return bool(NEWS_RE.search(text or ""))


def has_cta(text: str) -> bool:
    return bool(CTA_RE.search(text or "") or AD_TAG_RE.search(text or ""))


def news_exempt(text: str) -> bool:
    """True when the post reads as news/exposure and has no ad call-to-action."""
    if not is_news(text):
        return False
    return not has_cta(text)


def _handle_names(text: str) -> list[str]:
    return [m.group(0)[1:].casefold() for m in HANDLE_RE.finditer(text or "")]


def _external_tme(text: str) -> list[str]:
    """t.me links excluding this channel's own 此前文章 self-links."""
    out = []
    for m in TME_RE.findall(text or ""):
        if re.search(r"(?:t\.me|telegram\.me)/svip(?:/|$)", m, re.I):
            continue
        out.append(m)
    return out


def contact_heavy(text: str) -> bool:
    """Multiple *external* contact channels plus service wording → treated as an ad.

    Channel self-links (t.me/svip/123) are ignored so news posts that cite earlier
    articles are not flagged just for those URLs.
    """
    handles = set(_handle_names(text))
    tme = _external_tme(text)
    wame = WAME_RE.findall(text or "")
    phones = PHONE_RE.findall(text or "")
    contacts = len(handles) + len(tme) + len(wame) + len(phones)
    if contacts < 2:
        return False
    # Require a service / venue / CTA pitch — not just a list of mentions in a news story.
    return bool(SERVICE_WORDS_RE.search(text or "") or VENUE_WORD_RE.search(text or "") or has_cta(text))


def domain_or_handle_hit(text: str, hay: str) -> Optional[str]:
    m = BLOCK_DOMAIN_RE.search(text or "")
    if m:
        return m.group(0)
    for dom in BLOCK_DOMAINS:
        if tg_norm(dom) and tg_norm(dom) in hay:
            return dom
    for h in _handle_names(text):
        if h in BLOCK_HANDLES:
            return "@" + h
    return None


def venue_ad_hit(text: str, hay: str) -> Optional[str]:
    if VENUE_STRONG_RE.search(text or ""):
        return "会所/色情服务广告"
    # 会所/按摩/SPA… only as an ad when contact/CTA is also present.
    if VENUE_WORD_RE.search(text or "") and (
        has_cta(text) or contact_heavy(text) or SERVICE_WORDS_RE.search(text or "")
    ):
        if re.search(r"会所|选妃|上门服务|私密", text or ""):
            return "会所/色情服务广告"
        if re.search(r"按摩|spa|ktv|宵夜|陪玩", text or "", re.I):
            return "会所/色情服务广告"
    return None


def lottery_hit(text: str, hay: str) -> Optional[str]:
    if LOTTERY_HARD_RE.search(text or ""):
        return "彩票/开奖广告"
    if LOTTERY_BALLS_RE.search(text or "") and re.search(r"开奖|彩票|特码|期号|开出", text or ""):
        return "彩票/开奖广告"
    return None


def sponsor_hit(text: str) -> Optional[str]:
    if AD_TAG_RE.search(text or ""):
        tag = AD_TAG_RE.search(text or "")
        return f"广告标签 {tag.group(0)}" if tag else "广告标签"
    if SPONSOR_RE.search(text or ""):
        return "赞助/商务广告"
    if re.search(r"商务合作|付费推广|广告投放", text or ""):
        if contact_heavy(text) or SERVICE_WORDS_RE.search(text or ""):
            return "赞助/商务广告"
    return None


def adv_block_reason(text: str) -> Optional[str]:
    """Return a short reason if the haystack looks like a clear ad, else None.

    News/exposure posts are exempt unless an advertising CTA (or ad tag /
    contact-heavy service pitch) is also present.
    """
    text = text or ""
    hay = tg_norm(text)
    if not hay:
        return None
    # Ad tags and learned domains always count (they're not news language).
    tag = AD_TAG_RE.search(text)
    if tag:
        return f"命中「{tag.group(0)}」"
    hit = domain_or_handle_hit(text, hay)
    if hit:
        if news_exempt(text) and not domain_or_handle_hit(text, hay):
            return None
        # Domain/handle in a police report mentioning a site can be news;
        # only skip when it's clearly a takedown and has no CTA/contact pitch.
        if news_exempt(text):
            return None
        return f"命中广告域名/账号「{hit}」"
    if news_exempt(text):
        return None
    for fn in (sponsor_hit, venue_ad_hit, lottery_hit):
        if fn is venue_ad_hit or fn is lottery_hit:
            reason = fn(text, hay)
        else:
            reason = fn(text)
        if reason:
            return reason
    if contact_heavy(text):
        return "联系方式密集的广告"
    return None


def collect_extra_text(links: Optional[Iterable[str]] = None, buttons: Optional[Iterable[dict]] = None) -> str:
    """Flatten button labels/URLs and extra link hrefs into filterable text."""
    parts: list[str] = []
    for href in links or []:
        if href:
            parts.append(str(href))
    for btn in buttons or []:
        if not isinstance(btn, dict):
            continue
        t = (btn.get("text") or "").strip()
        u = (btn.get("url") or btn.get("href") or "").strip()
        if t:
            parts.append(t)
        if u:
            parts.append(u)
    return "\n".join(parts)


def ocr_available() -> bool:
    from shutil import which
    return which("tesseract") is not None


def ocr_image(path: Path, timeout: int = OCR_TIMEOUT) -> str:
    """Cheap OCR via tesseract chi_sim; empty on any failure/timeout."""
    try:
        if not path.is_file() or path.stat().st_size > OCR_MAX_BYTES:
            return ""
    except OSError:
        return ""
    try:
        proc = subprocess.run(
            [
                "nice", "-n", "15",
                "tesseract", str(path), "stdout",
                "-l", OCR_LANGS, "--psm", "6",
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    return (proc.stdout or b"").decode("utf-8", "replace").strip()


def ocr_media_files(paths: Iterable[Path], limit: int = OCR_MAX_IMAGES) -> str:
    chunks: list[str] = []
    n = 0
    for path in paths:
        if n >= limit:
            break
        text = ocr_image(Path(path))
        if text:
            chunks.append(text)
            n += 1
    return "\n".join(chunks)


# Seed patterns added once as block rules (plus code detectors above).
SEED_V3_BLOCK = (
    "#友情广告",
    "#付费广告",
    "#此次活动由",
    "独家赞助",
    "老牌赞助商",
    "y3666.com",
    "122.VIP",
    "@Y3Y3",
    "@Y3_69bot",
    "@PG321",
    "@ZSJ44",
    "@ZSJ444",
    "@TG78",
    "@DF78",
    "@KF78com",
    "@DL78com",
    "开奖结果",
    "特码",
    "六合彩",
    "快三",
    "PK10",
    "大乐透",
    "双色球",
    "选妃",
    "真照上门",
    "上门服务",
)

SEED_V3_GAMBLE = (
    ("开奖结果", 4),
    ("开出了", 4),
    ("特码", 4),
    ("期号", 3),
    ("大乐透", 4),
    ("双色球", 4),
    ("PK10", 4),
    ("极速号码", 4),
    ("号码轮盘", 3),
    ("选妃", 4),
    ("上门服务", 4),
)
