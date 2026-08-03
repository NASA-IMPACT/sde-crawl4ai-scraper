"""Same-site and asset URL filters."""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import urlparse, urlunparse

from crawl4ai.deep_crawling.filters import URLFilter

from sde_crawler.extract import path_kind

_FTP_PATH = re.compile(r"(^|/)FTP(/|$)", re.I)


def normalize_seed(url: str) -> str:
    url = url.strip()
    if not urlparse(url).scheme:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid seed URL: {url}")
    if is_ftp_url(url):
        raise ValueError(f"FTP URLs are not scraped: {url}")
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path or "/", parsed.params, parsed.query, "")
    )


def apex_host(url: str) -> str:
    host = urlparse(url).netloc.lower().split("@")[-1]
    return host.removeprefix("www.")


def is_ftp_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme in {"ftp", "ftps"}:
        return True
    host = (parsed.netloc or "").lower().split("@")[-1]
    if host.startswith("ftp.") or host.startswith("ftp2."):
        return True
    return bool(_FTP_PATH.search(parsed.path or ""))


def is_same_site(url: str, apex: str, include_subdomains: bool = False) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if is_ftp_url(url):
        return False
    host = parsed.netloc.lower().split("@")[-1]
    if include_subdomains:
        return host == apex or host.endswith("." + apex)
    return host in {apex, f"www.{apex}"}


class SameSiteFilter(URLFilter):
    def __init__(self, apex: str, include_subdomains: bool = False):
        super().__init__(name="SameSiteFilter")
        self.apex = apex.lower()
        self.include_subdomains = include_subdomains

    def apply(self, url: str) -> bool:
        passed = is_same_site(url, self.apex, self.include_subdomains)
        self._update_stats(passed)
        return passed


class FtpFilter(URLFilter):
    def __init__(self):
        super().__init__(name="FtpFilter")

    def apply(self, url: str) -> bool:
        passed = not is_ftp_url(url)
        self._update_stats(passed)
        return passed


class AssetFilter(URLFilter):
    def apply(self, url: str) -> bool:
        passed = path_kind(url) != "skip"
        self._update_stats(passed)
        return passed


class RobotsObeyFilter(URLFilter):
    def __init__(self, would_disallow: Callable[[str], bool]):
        super().__init__(name="RobotsObeyFilter")
        self._would_disallow = would_disallow

    def apply(self, url: str) -> bool:
        passed = not self._would_disallow(url)
        self._update_stats(passed)
        return passed
