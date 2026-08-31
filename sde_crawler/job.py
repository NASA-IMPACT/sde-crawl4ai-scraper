"""Merge job JSON with crawl defaults; derive local and S3 paths."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sde_crawler.defaults import (
    DEFAULT_CHECKPOINT_PAGES,
    DEFAULT_CHECKPOINT_SECONDS,
    DEFAULT_DELAY,
    DEFAULT_MAX_PAGES,
    DEFAULT_URL_TIMEOUT,
)
from sde_crawler.scope import apex_host, normalize_seed

JOB_DEFAULTS: dict[str, Any] = {
    "max_pages": DEFAULT_MAX_PAGES,
    "delay": DEFAULT_DELAY,
    "concurrent_requests": 1,
    "obey_robots": False,
    "include_subdomains": False,
    "url_timeout": DEFAULT_URL_TIMEOUT,
    "checkpoint_pages": DEFAULT_CHECKPOINT_PAGES,
    "checkpoint_seconds": DEFAULT_CHECKPOINT_SECONDS,
}

_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def collection_id_from_seed(seed: str) -> str:
    host = apex_host(normalize_seed(seed))
    return _SLUG_RE.sub("_", host).strip("._") or "collection"


def load_job_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Job file must be a JSON object: {path}")
    return data


def normalize_url_list(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("urls must be a non-empty list")
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        url = normalize_seed(str(item))
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def merge_job(payload: dict, *, root: Path) -> dict:
    has_seed = bool(payload.get("seed"))
    has_urls = payload.get("urls") is not None
    if not has_seed and not has_urls:
        raise ValueError("Job JSON must include a 'seed' URL or a 'urls' list")

    urls = normalize_url_list(payload["urls"]) if has_urls else None
    if has_seed:
        seed = normalize_seed(str(payload["seed"]))
    else:
        seed = urls[0]

    coll_id = str(payload.get("collection_id") or collection_id_from_seed(seed))

    cfg = {**JOB_DEFAULTS}
    for key, value in payload.items():
        if key in {"seed", "collection_id", "urls"} or value is None:
            continue
        cfg[key] = value

    cfg["seed"] = seed
    cfg["collection_id"] = coll_id
    cfg["urls"] = urls

    if not payload.get("output"):
        cfg["output"] = str(root / "output" / "collections" / f"{coll_id}.jsonl")
    if not payload.get("failures_log"):
        cfg["failures_log"] = str(root / "logs" / "collections" / f"{coll_id}_failures.jsonl")

    for key in ("output", "failures_log"):
        p = Path(cfg[key])
        if not p.is_absolute():
            cfg[key] = str(root / p)

    cfg["s3_documents_key"] = s3_keys_for_collection(coll_id)["documents"]
    return cfg


def s3_keys_for_collection(coll_id: str) -> dict[str, str]:
    return {
        "documents": f"scraped_collections/{coll_id}.json",
        "failures": f"failure_logs/{coll_id}_failures.jsonl",
        "summary": f"failure_logs/{coll_id}_failures_summary.json",
    }
