"""Append-only document JSONL with S3 checkpoints."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def write_json_array(jsonl_path: Path, json_path: Path) -> Path:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("r", encoding="utf-8") as src, json_path.open("w", encoding="utf-8") as dst:
        dst.write("[\n")
        first = True
        for line in src:
            line = line.strip()
            if not line:
                continue
            if not first:
                dst.write(",\n")
            dst.write(line)
            first = False
        dst.write("\n]\n")
    return json_path


class DocumentLog:
    def __init__(
        self,
        path: str | Path,
        *,
        bucket: str | None = None,
        s3_key: str | None = None,
        checkpoint_pages: int = 100,
        checkpoint_seconds: float = 300.0,
        on_checkpoint: Callable[[int, str], None] | None = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self.count = 0
        self._bucket = bucket or None
        self._s3_key = s3_key or None
        self._checkpoint_pages = max(1, int(checkpoint_pages))
        self._checkpoint_seconds = max(1.0, float(checkpoint_seconds))
        self._since_upload = 0
        self._last_upload = time.monotonic()
        self._on_checkpoint = on_checkpoint

    @property
    def array_path(self) -> Path:
        return self.path.with_suffix(".json")

    @property
    def checkpoint_seconds(self) -> float:
        return self._checkpoint_seconds

    @property
    def s3_enabled(self) -> bool:
        return bool(self._bucket and self._s3_key)

    def write(self, doc: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
        self.count += 1
        self._since_upload += 1
        if self._since_upload >= self._checkpoint_pages:
            self.flush_s3()

    def flush_if_due(self) -> None:
        if self._since_upload <= 0:
            return
        if (time.monotonic() - self._last_upload) >= self._checkpoint_seconds:
            self.flush_s3()

    def flush_s3(self) -> str | None:
        if not self.s3_enabled or self.count == 0 or not self.path.exists():
            return None
        try:
            from sde_crawler.s3upload import upload_file

            array_path = write_json_array(self.path, self.array_path)
            uri = upload_file(bucket=self._bucket, path=array_path, key=self._s3_key)
            array_path.unlink(missing_ok=True)
        except Exception:
            logger.warning("S3 document checkpoint failed", exc_info=True)
            return None
        self._since_upload = 0
        self._last_upload = time.monotonic()
        if self._on_checkpoint:
            self._on_checkpoint(self.count, uri)
        return uri
