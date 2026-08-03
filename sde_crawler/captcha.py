"""Challenge-page detection (log only)."""

from __future__ import annotations

import re

BLOCK_TITLE = re.compile(
    r"(attention required|just a moment|access denied|captcha|"
    r"verify you are human|security check|bot detection|are you a robot)",
    re.I,
)

CHALLENGE_MARKERS = (
    "cf-browser-verification",
    "challenge-platform",
    "turnstile",
    "g-recaptcha",
    "hcaptcha",
    "data-sitekey",
)


def is_challenge_page(title: str, html: str) -> tuple[bool, str]:
    if title and BLOCK_TITLE.search(title):
        return True, "challenge_title"

    lower = (html or "").lower()
    for marker in CHALLENGE_MARKERS:
        if marker not in lower:
            continue
        body_len = len(re.sub(r"\s+", " ", lower))
        if body_len < 2500 or marker in (
            "cf-browser-verification",
            "challenge-platform",
            "turnstile",
        ):
            return True, f"challenge_marker:{marker}"
    return False, ""
