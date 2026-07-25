from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "from", "source", "ref", "referrer", "tracking", "trk",
}


def normalize_url(value: str | None) -> str:
    if not value:
        return ""
    parts = urlsplit(value.strip())
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
    ]
    path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def normalize_title(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[\[【（(][^]】）)]*(?:急聘|热招|猎头|代招|渠道|编号|id)[^]】）)]*[]】）)]", " ", text)
    text = re.sub(r"\b(?:job\s*)?(?:id|no)\s*[:#-]?\s*[a-z0-9-]+\b", " ", text)
    replacements = {
        r"\bvice president\b": "vp",
        r"\bmanaging director\b": "md",
        r"\bhead of\b": "head",
        r"\bgeneral manager\b": "gm",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
    return _compact(text)


def normalize_company(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(
        r"(股份有限公司|有限责任公司|有限公司|集团|控股|科技|技术|中国|china|"
        r"co\.?\s*,?\s*ltd\.?|company|corporation|corp\.?|inc\.?|group|holdings?)$",
        "",
        text,
    )
    return _compact(text)


def normalize_location(value: str | None) -> str:
    text = _compact((value or "").lower())
    aliases = {
        "上海市": "上海", "北京市": "北京", "深圳市": "深圳", "广州市": "广州",
        "shanghai": "上海", "beijing": "北京", "shenzhen": "深圳", "guangzhou": "广州",
    }
    return aliases.get(text, text)


def content_hash(value: str | None) -> str:
    normalized = _compact((value or "").lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def stable_id(prefix: str, *parts: str | None) -> str:
    payload = "|".join((part or "").strip().lower() for part in parts)
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


def text_similarity(left: str | None, right: str | None) -> float:
    a, b = _compact((left or "").lower()), _compact((right or "").lower())
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def fingerprint(company: str | None, title: str | None, location: str | None, jd_hash: str | None) -> str:
    return stable_id(
        "fp",
        normalize_company(company),
        normalize_title(title),
        normalize_location(location),
        jd_hash,
    )


def _compact(value: str) -> str:
    return re.sub(r"[\s\-_—·|/\\:：,，.。]+", " ", value).strip()
