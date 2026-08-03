"""Upload crawl artifacts to S3."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def upload_job_artifacts(
    *,
    bucket: str,
    coll_id: str,
    documents_path: Path,
    failures_path: Path,
    summary_path: Path,
) -> dict[str, str]:
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is required for S3 upload") from exc

    from sde_crawler.job import s3_keys_for_collection

    keys = s3_keys_for_collection(coll_id)
    client = boto3.client("s3")
    uploaded: dict[str, str] = {}

    for label, path, key in (
        ("documents", documents_path, keys["documents"]),
        ("failures", failures_path, keys["failures"]),
        ("summary", summary_path, keys["summary"]),
    ):
        if not path.exists():
            logger.warning("missing %s: %s", label, path)
            continue
        client.upload_file(str(path), bucket, key)
        uploaded[label] = f"s3://{bucket}/{key}"

    return uploaded
