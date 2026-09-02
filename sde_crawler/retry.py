"""Post-pass retry helpers."""

from __future__ import annotations

import json
from pathlib import Path

RETRYABLE_REASONS = frozenset(
    {
        "url_timeout",
        "crawl_unsuccessful",
        "download_error",
        "http_rate_limit",
        "http_429",
        "http_503",
        "http_502",
        "http_504",
        "http_500",
    }
)


def is_retryable(reason: str) -> bool:
    return reason in RETRYABLE_REASONS


def load_document_urls(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    urls: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            url = json.loads(line).get("url")
        except json.JSONDecodeError:
            continue
        if url:
            urls.add(str(url))
    return urls


def load_retry_urls(path: Path, *, skip_urls: set[str] | None = None) -> list[str]:
    skip = skip_urls or set()
    if not path.is_file():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = str(record.get("url") or "").strip()
        reason = str(record.get("reason") or "")
        if not url or url in skip or url in seen:
            continue
        if not is_retryable(reason):
            continue
        seen.add(url)
        out.append(url)
    return out
