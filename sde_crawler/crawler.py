"""Crawl orchestration on Crawl4AI."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
from crawl4ai.deep_crawling.filters import FilterChain

from sde_crawler.captcha import is_challenge_page
from sde_crawler.console import JobLog
from sde_crawler.extract import (
    extract_page,
    file_title,
    path_kind,
    pdf_full_text,
    plain_full_text,
    response_kind,
)
from sde_crawler.failures import FailureLog
from sde_crawler.robots import RobotsAccountant
from sde_crawler.scope import (
    AssetFilter,
    FtpFilter,
    RobotsObeyFilter,
    SameSiteFilter,
    apex_host,
    normalize_seed,
)

logger = logging.getLogger(__name__)

DEFAULT_DELAY = 0.25
DEFAULT_MAX_PAGES = 100_000
MAX_PAGES_CAP = 100_000
UNLIMITED_DEPTH = 1_000_000


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _failure_reason(status: int | None) -> str:
    if status == 404:
        return "http_404"
    if status == 403:
        return "http_403"
    if status in {401, 407}:
        return "http_auth"
    if status in {429, 503}:
        return "http_rate_limit"
    if status is not None and status >= 400:
        return f"http_{status}"
    return "crawl_unsuccessful"


class SeedCrawler:
    """Same-site BFS crawl: HTML via Chromium; PDF/plain via HTTP."""

    def __init__(self, config: dict):
        if not config.get("seed"):
            raise ValueError("Config must include a 'seed' URL")

        self.seed = normalize_seed(config["seed"])
        self.apex = apex_host(self.seed)

        max_pages = config.get("max_pages", DEFAULT_MAX_PAGES)
        n = int(max_pages)
        if n <= 0:
            raise ValueError("max_pages must be a positive integer")
        if n > MAX_PAGES_CAP:
            raise ValueError(f"max_pages cannot exceed {MAX_PAGES_CAP}")
        self.max_pages = n

        depth = config.get("depth_limit", None)
        if depth is None or depth == "" or str(depth).strip().lower() in {"none", "unlimited", "inf"}:
            self.depth_limit = UNLIMITED_DEPTH
            self.depth_unlimited = True
        else:
            self.depth_limit = int(depth)
            self.depth_unlimited = False
            if self.depth_limit < 0:
                raise ValueError("depth_limit must be >= 0")

        self.delay = float(config.get("delay", DEFAULT_DELAY))
        self.concurrent_requests = max(1, int(config.get("concurrent_requests", 1)))
        self.include_subdomains = _as_bool(config.get("include_subdomains"), False)
        self.obey_robots = _as_bool(config.get("obey_robots"), False)
        self.verbose = _as_bool(config.get("verbose"), False)

        self.output_path = Path(config.get("output", "output/documents.json"))
        self.failures_log = Path(config.get("failures_log", "logs/failures.jsonl"))
        self.collection_id = str(config.get("collection_id") or "collection")
        base = config.get("crawl4ai_base")
        self.crawl4ai_base = Path(base) if base else Path.cwd() / ".crawl4ai_runtime" / self.collection_id

        self.robots = RobotsAccountant()
        self.failures = FailureLog(self.failures_log)
        self.documents: list[dict] = []
        log_file = config.get("job_log_file")
        self.joblog = JobLog(verbose=self.verbose, file=log_file)
        self._http: httpx.AsyncClient | None = None

    def _build_filter_chain(self) -> FilterChain:
        filters: list = [
            SameSiteFilter(self.apex, include_subdomains=self.include_subdomains),
            FtpFilter(),
            AssetFilter(),
        ]
        if self.obey_robots:
            filters.append(RobotsObeyFilter(self.robots.would_disallow))
        return FilterChain(filters)

    def _build_run_config(self) -> CrawlerRunConfig:
        strategy = BFSDeepCrawlStrategy(
            max_depth=self.depth_limit,
            include_external=False,
            max_pages=self.max_pages + 1,
            filter_chain=self._build_filter_chain(),
        )

        return CrawlerRunConfig(
            deep_crawl_strategy=strategy,
            cache_mode=CacheMode.BYPASS,
            wait_until="domcontentloaded",
            page_timeout=60_000,
            mean_delay=self.delay,
            max_range=0.0,
            semaphore_count=self.concurrent_requests,
            check_robots_txt=self.obey_robots,
            exclude_external_links=True,
            stream=True,
            verbose=False,
        )

    def _at_cap(self) -> bool:
        return len(self.documents) >= self.max_pages

    def _emit_document(
        self,
        *,
        url: str,
        title: str,
        full_text: str,
        content_type: str,
        depth: int | None,
        status_label: str = "ok",
        detail: str = "",
    ) -> None:
        if self._at_cap():
            return
        self.robots.note_crawl(url)
        self.documents.append(
            {
                "url": url,
                "title": title or url,
                "full_text": full_text,
                "content_type": content_type,
                "seed": self.seed,
                "host": urlparse(url).netloc.lower(),
                "depth": depth if depth is not None else 0,
            }
        )
        self.joblog.page(
            status=status_label,
            url=url,
            depth=depth,
            detail=detail,
            max_pages=self.max_pages,
        )

    async def _download_bytes(self, url: str) -> tuple[bytes, str, int]:
        assert self._http is not None
        resp = await self._http.get(url)
        ctype = resp.headers.get("content-type", "")
        return resp.content, ctype, resp.status_code

    async def _emit_file(self, url: str, depth: int | None, preferred_kind: str) -> None:
        """Download PDF/plain file and extract text."""
        if self._at_cap():
            return
        if self.obey_robots and self.robots.would_disallow(url):
            return

        try:
            body, ctype, status = await self._download_bytes(url)
        except Exception as exc:
            self.failures.write(
                url=url,
                reason="download_error",
                detail=repr(exc),
            )
            self.joblog.page(
                status="fail",
                url=url,
                depth=depth,
                detail="download_error",
                max_pages=self.max_pages,
            )
            return

        if status >= 400:
            reason = _failure_reason(status)
            self.failures.write(url=url, reason=reason, detail=f"HTTP {status}", status=status)
            self.joblog.page(
                status="fail",
                url=url,
                depth=depth,
                detail=reason,
                max_pages=self.max_pages,
            )
            return

        kind = response_kind(url, ctype, body)
        if kind == "skip":
            return

        try:
            if kind == "pdf" or preferred_kind == "pdf":
                text = pdf_full_text(body)
                content_type = "application/pdf"
                label = "pdf"
            else:
                text = plain_full_text(body)
                content_type = (ctype.split(";")[0].strip() or "text/plain")
                label = "plain"
        except Exception as exc:
            self.failures.write(url=url, reason="extract_error", detail=repr(exc))
            self.joblog.page(
                status="fail",
                url=url,
                depth=depth,
                detail="extract_error",
                max_pages=self.max_pages,
            )
            return

        if not text:
            self.failures.write(
                url=url,
                reason="empty_extract",
                detail=f"kind={kind or preferred_kind}",
            )
            self.joblog.page(
                status="empty",
                url=url,
                depth=depth,
                detail="empty file extract",
                max_pages=self.max_pages,
            )
            return

        self._emit_document(
            url=url,
            title=file_title(url),
            full_text=text,
            content_type=content_type,
            depth=depth,
            status_label=label,
        )

    async def _process_result(self, result) -> None:
        url = result.url or ""
        status = result.status_code
        html = result.html or result.cleaned_html or ""
        meta = result.metadata or {}
        depth = meta.get("depth")
        kind = path_kind(url)

        if kind in {"pdf", "plain"}:
            await self._emit_file(url, depth, preferred_kind=kind)
            return

        if not result.success:
            try:
                body, ctype, http_status = await self._download_bytes(url)
                rk = response_kind(url, ctype, body)
                if rk in {"pdf", "plain"} and http_status < 400:
                    await self._emit_file(url, depth, preferred_kind=rk)
                    return
            except Exception:
                pass

            title, _ = extract_page(html, meta)
            reason = _failure_reason(status)
            self.failures.write(
                url=url,
                reason=reason,
                detail=result.error_message or "",
                title=title,
                status=status,
            )
            self.joblog.page(
                status="fail",
                url=url,
                depth=depth,
                detail=reason,
                max_pages=self.max_pages,
            )
            return

        title, full_text = extract_page(html, meta)

        challenged, challenge_reason = is_challenge_page(title, html)
        if challenged:
            self.failures.write(
                url=url,
                reason=challenge_reason,
                detail="Challenge page skipped",
                title=title,
                status=status,
            )
            self.joblog.page(
                status="challenge",
                url=url,
                depth=depth,
                detail=challenge_reason,
                max_pages=self.max_pages,
            )
            return

        if not full_text:
            self.failures.write(
                url=url,
                reason="empty_extract",
                detail="No visible body text",
                title=title,
                status=status,
            )
            self.joblog.page(
                status="empty",
                url=url,
                depth=depth,
                detail="no visible text",
                max_pages=self.max_pages,
            )
            return

        self._emit_document(
            url=url,
            title=title or url,
            full_text=full_text,
            content_type="text/html",
            depth=depth,
            status_label="ok",
        )

    async def _consume_results(self, results) -> None:
        if results is None:
            return
        if hasattr(results, "__aiter__"):
            async for result in results:
                await self._process_result(result)
                if self._at_cap():
                    break
            return
        if isinstance(results, (list, tuple)):
            for result in results:
                await self._process_result(result)
                if self._at_cap():
                    break
            return
        try:
            for result in results:
                await self._process_result(result)
                if self._at_cap():
                    break
        except TypeError:
            await self._process_result(results)

    async def run(self) -> dict:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.failures_log.parent.mkdir(parents=True, exist_ok=True)

        self.joblog.banner(
            seed=self.seed,
            depth_limit="unlimited" if self.depth_unlimited else self.depth_limit,
            max_pages=self.max_pages,
            delay=self.delay,
            concurrent=self.concurrent_requests,
            obey_robots=self.obey_robots,
        )

        await self.robots.fetch(self.seed)
        if self.robots.robots_fetch_ok:
            self.joblog.robots(ok=True)
        else:
            self.joblog.robots(ok=False)

        browser_config = BrowserConfig(headless=True, verbose=False)
        run_config = self._build_run_config()
        self.crawl4ai_base.mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=120.0,
            headers={"User-Agent": "SDECrawler/0.1 (+research; polite)"},
        ) as http:
            self._http = http
            if path_kind(self.seed) in {"pdf", "plain"}:
                await self._emit_file(self.seed, depth=0, preferred_kind=path_kind(self.seed))
            else:
                async with AsyncWebCrawler(
                    config=browser_config,
                    base_directory=str(self.crawl4ai_base),
                    thread_safe=True,
                ) as crawler:
                    results = await crawler.arun(url=self.seed, config=run_config)
                    await self._consume_results(results)
            self._http = None

        self._write_documents()
        summary = self._write_summary()
        summary_path = self.failures_log.with_name(self.failures_log.stem + "_summary.json")
        self.joblog.footer(
            documents=len(self.documents),
            failures=self.failures.count,
            robots_would_block=summary.get("urls_that_robots_would_disallow", 0),
            output=str(self.output_path),
            failures_log=str(self.failures_log),
            summary_path=str(summary_path),
            failures_by_bucket=summary.get("failures_by_bucket") or {},
        )
        return summary

    def _write_documents(self) -> None:
        self.output_path.write_text(
            json.dumps(self.documents, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_summary(self) -> dict:
        tallies = self.failures.tallies()
        summary = {
            **self.robots.summary(),
            "documents_scraped": len(self.documents),
            "failures_logged": self.failures.count,
            "failures_by_reason": tallies["failures_by_reason"],
            "failures_by_bucket": tallies["failures_by_bucket"],
            "seed": self.seed,
            "apex_host": self.apex,
            "obey_robots": self.obey_robots,
            "delay": self.delay,
            "concurrent_requests": self.concurrent_requests,
            "depth_limit": None if self.depth_unlimited else self.depth_limit,
            "max_pages": self.max_pages,
        }
        path = self.failures_log.with_name(self.failures_log.stem + "_summary.json")
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary
