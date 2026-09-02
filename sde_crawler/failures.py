"""Failure JSONL log and reason tallies."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def _bucket(reason: str) -> str:
    if reason.startswith("challenge"):
        return "challenge"
    if reason.startswith("http_"):
        if reason in {"http_404"}:
            return "not_found"
        if reason in {"http_401", "http_403", "http_auth"}:
            return "access_denied"
        if reason in {"http_429", "http_503", "http_rate_limit"}:
            return "rate_limit"
        return "http_other"
    if reason == "crawl_unsuccessful":
        return "crawl_unsuccessful"
    if reason in {"empty_extract", "extract_error"}:
        return "extract"
    if reason == "download_error":
        return "download"
    if reason == "url_timeout":
        return "timeout"
    if reason == "skipped_type":
        return "skipped"
    return "other"


class FailureLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self.count = 0
        self._by_reason: Counter[str] = Counter()
        self._by_bucket: Counter[str] = Counter()

    def write(
        self,
        *,
        url: str,
        reason: str,
        detail: str = "",
        title: str = "",
        status: int | None = None,
    ) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "url": url,
            "reason": reason,
            "status": status,
            "detail": detail,
            "title": title,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.count += 1
        self._by_reason[reason] += 1
        self._by_bucket[_bucket(reason)] += 1

    def tallies(self) -> dict:
        return {
            "failures_by_reason": dict(self._by_reason.most_common()),
            "failures_by_bucket": dict(self._by_bucket.most_common()),
        }
