"""robots.txt fetch and would-block accounting."""

from __future__ import annotations

import logging
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)


class RobotsAccountant:
    def __init__(self, user_agent: str = "*"):
        self.user_agent = user_agent
        self._parser: RobotFileParser | None = None
        self.would_block_count = 0
        self.robots_fetch_ok = False
        self.robots_fetch_fail = False
        self.blocked_samples: list[str] = []

    async def fetch(self, seed: str) -> None:
        robots_url = urljoin(seed, "/robots.txt")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                resp = await client.get(robots_url)
            if resp.status_code == 200 and resp.text:
                rp = RobotFileParser()
                rp.parse(resp.text.splitlines())
                self._parser = rp
                self.robots_fetch_ok = True
            else:
                self.robots_fetch_fail = True
        except Exception as exc:
            self.robots_fetch_fail = True
            logger.debug("robots.txt fetch failed: %s", exc)

    def would_disallow(self, url: str) -> bool:
        if self._parser is None:
            return False
        try:
            return not self._parser.can_fetch(self.user_agent, url)
        except Exception:
            return False

    def note_crawl(self, url: str) -> None:
        if self.would_disallow(url):
            self.would_block_count += 1
            if len(self.blocked_samples) < 20:
                self.blocked_samples.append(url)

    def summary(self) -> dict:
        return {
            "robots_fetch_ok": self.robots_fetch_ok,
            "robots_fetch_fail": self.robots_fetch_fail,
            "urls_that_robots_would_disallow": self.would_block_count,
            "sample_disallowed_urls": list(self.blocked_samples),
        }
