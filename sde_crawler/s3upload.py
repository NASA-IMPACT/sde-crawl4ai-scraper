"""Upload crawl artifacts to S3."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def upload_file(*, bucket: str, path: Path, key: str) -> str:
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is required for S3 upload") from exc

    if not path.exists():
        raise FileNotFoundError(path)
    boto3.client("s3").upload_file(str(path), bucket, key)
    return f"s3://{bucket}/{key}"


def upload_job_artifacts(
    *,
    bucket: str,
    coll_id: str,
    documents_path: Path,
    failures_path: Path,
    summary_path: Path,
) -> dict[str, str]:
    from sde_crawler.job import s3_keys_for_collection

    keys = s3_keys_for_collection(coll_id)
    uploaded: dict[str, str] = {}

    for label, path, key in (
        ("documents", documents_path, keys["documents"]),
        ("failures", failures_path, keys["failures"]),
        ("summary", summary_path, keys["summary"]),
    ):
        if not path.exists():
            logger.warning("missing %s: %s", label, path)
            continue
        uploaded[label] = upload_file(bucket=bucket, path=path, key=key)

    return uploaded
