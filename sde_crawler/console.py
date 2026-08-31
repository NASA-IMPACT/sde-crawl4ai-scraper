"""Crawl progress on stdout."""

from __future__ import annotations

import sys
from urllib.parse import urlparse


def _short_url(url: str, max_len: int = 72) -> str:
    if len(url) <= max_len:
        return url
    return url[: max_len - 1] + "..."


class JobLog:
    def __init__(self, *, verbose: bool = False, file=None):
        self.verbose = verbose
        self._out = file or sys.stdout
        self.seen = 0
        self.ok = 0
        self.failed = 0

    def _write(self, line: str = "") -> None:
        print(line, file=self._out, flush=True)

    def banner(
        self,
        *,
        seed: str,
        depth_limit: int | str,
        max_pages: int,
        delay: float,
        concurrent: int,
        obey_robots: bool,
        url_count: int | None = None,
        url_timeout: float = 180.0,
    ) -> None:
        host = urlparse(seed).netloc
        self._write()
        self._write("-" * 60)
        self._write("  SDE crawl")
        self._write("-" * 60)
        if url_count is not None:
            self._write(f"  mode        url_list ({url_count} urls)")
        self._write(f"  seed        {seed}")
        self._write(f"  host        {host}")
        if url_count is None:
            self._write(f"  depth_limit {depth_limit}")
        self._write(f"  max_pages   {max_pages}")
        self._write(f"  delay       {delay}s   concurrency {concurrent}")
        self._write(f"  url_timeout {url_timeout}s")
        self._write(f"  obey_robots {obey_robots}")
        self._write("-" * 60)
        self._write()

    def robots(self, *, ok: bool, detail: str = "") -> None:
        if ok:
            self._write(f"  robots      loaded{('  (' + detail + ')') if detail else ''}")
        else:
            self._write(f"  robots      unavailable{('  (' + detail + ')') if detail else ''}")
        self._write()
        self._write(f"  {'#':<5} {'status':<10} {'depth':<6} url")
        self._write(f"  {'-'*5} {'-'*10} {'-'*6} {'-'*40}")

    def page(
        self,
        *,
        status: str,
        url: str,
        depth: int | None = None,
        detail: str = "",
        max_pages: int,
    ) -> None:
        self.seen += 1
        if status in {"ok", "pdf", "plain"}:
            self.ok += 1
        else:
            self.failed += 1

        depth_s = "-" if depth is None else str(depth)
        line = f"  {self.seen:<5} {status:<10} {depth_s:<6} {_short_url(url)}"
        self._write(line)
        if detail and (self.verbose or status not in {"ok", "pdf", "plain"}):
            self._write(f"        {detail}")

        if status in {"ok", "pdf", "plain"} and self.ok % 25 == 0 and self.ok > 0:
            self._write(f"  ... {self.ok} docs / {self.failed} failed  (cap {max_pages})")

    def checkpoint(self, *, documents: int, uri: str) -> None:
        self._write(f"  checkpoint  {documents} docs -> {uri}")

    def footer(
        self,
        *,
        documents: int,
        failures: int,
        robots_would_block: int,
        output: str,
        failures_log: str,
        summary_path: str,
        failures_by_bucket: dict | None = None,
    ) -> None:
        self._write()
        self._write("-" * 60)
        self._write("  Done")
        self._write("-" * 60)
        self._write(f"  documents   {documents}")
        self._write(f"  failures    {failures}")
        self._write(f"  robots would-disallow  {robots_would_block}")
        if failures_by_bucket:
            self._write("  failure buckets:")
            for name, n in failures_by_bucket.items():
                self._write(f"    {name:<16} {n}")
        self._write()
        self._write(f"  documents -> {output}")
        self._write(f"  failures  -> {failures_log}")
        self._write(f"  summary   -> {summary_path}")
        self._write("-" * 60)
        self._write()
