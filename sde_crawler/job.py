"""Merge job JSON with crawl defaults; derive local and S3 paths."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sde_crawler.crawler import DEFAULT_DELAY, DEFAULT_MAX_PAGES
from sde_crawler.scope import apex_host, normalize_seed

JOB_DEFAULTS: dict[str, Any] = {
    "max_pages": DEFAULT_MAX_PAGES,
    "delay": DEFAULT_DELAY,
    "concurrent_requests": 1,
    "obey_robots": False,
    "include_subdomains": False,
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


def merge_job(payload: dict, *, root: Path) -> dict:
    if not payload.get("seed"):
        raise ValueError("Job JSON must include a 'seed' URL")

    seed = normalize_seed(str(payload["seed"]))
    coll_id = str(payload.get("collection_id") or collection_id_from_seed(seed))

    cfg = {**JOB_DEFAULTS}
    for key, value in payload.items():
        if key in {"seed", "collection_id"} or value is None:
            continue
        cfg[key] = value

    cfg["seed"] = seed
    cfg["collection_id"] = coll_id

    if not payload.get("output"):
        cfg["output"] = str(root / "output" / "collections" / f"{coll_id}.json")
    if not payload.get("failures_log"):
        cfg["failures_log"] = str(root / "logs" / "collections" / f"{coll_id}_failures.jsonl")

    for key in ("output", "failures_log"):
        p = Path(cfg[key])
        if not p.is_absolute():
            cfg[key] = str(root / p)

    return cfg


def s3_keys_for_collection(coll_id: str) -> dict[str, str]:
    return {
        "documents": f"scraped_collections/{coll_id}.json",
        "failures": f"failure_logs/{coll_id}_failures.jsonl",
        "summary": f"failure_logs/{coll_id}_failures_summary.json",
    }
