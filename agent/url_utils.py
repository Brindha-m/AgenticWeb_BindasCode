"""
URL helpers for the orchestrator — extract target site, compare URLs, block redundant navigations.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse


def _normalize(url: str) -> str:
    url = url.strip().rstrip(".,;)>\"'")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")
    return url


def extract_primary_url(task: str) -> str | None:
    """First canonical URL for the task (hidden prompt or plain user text)."""
    if not task:
        return None

    m = re.search(r"PRIMARY URL:\s*(https?://\S+)", task, re.IGNORECASE)
    if m:
        return _normalize(m.group(1))

    m = re.search(r"https?://[^\s\)\]>\"']+", task)
    if m:
        return _normalize(m.group(0))

    m = re.search(
        r"\b(?:www\.)?([a-z0-9][-a-z0-9.]*\.(?:gov\.in|nic\.in|co\.in|org\.in|com|in|org))\b",
        task,
        re.IGNORECASE,
    )
    if m:
        return _normalize(m.group(0))

    return None


def url_host(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def urls_equivalent(a: str, b: str) -> bool:
    """True when reload would not meaningfully change the page."""
    if not a or not b:
        return False
    try:
        pa, pb = urlparse(a), urlparse(b)
        ha = pa.netloc.lower().lstrip("www.")
        hb = pb.netloc.lower().lstrip("www.")
        if ha != hb:
            return False
        pa_path = (pa.path or "/").rstrip("/") or "/"
        pb_path = (pb.path or "/").rstrip("/") or "/"
        if pa_path == pb_path:
            return True
        # Treat homepage variants as equivalent
        if pa_path in ("/", "") and pb_path in ("/", ""):
            return True
        return False
    except Exception:
        return a.rstrip("/") == b.rstrip("/")


def same_site(a: str, b: str) -> bool:
    ha, hb = url_host(a), url_host(b)
    return bool(ha) and ha == hb
