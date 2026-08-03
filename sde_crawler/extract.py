"""URL classification and text extraction."""

from __future__ import annotations

import re
from io import BytesIO
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pypdf import PdfReader

SKIP_EXT = re.compile(
    r"\.(zip|rar|gz|tgz|7z|tar|bz2|xz|exe|dmg|iso|msi|deb|rpm|pkg|jar|war|"
    r"png|jpe?g|gif|svg|webp|ico|bmp|tiff?|avif|"
    r"mp3|mp4|m4v|avi|mov|wmv|webm|mkv|wav|mpg|mpeg|au|"
    r"css|js|mjs|map|woff2?|ttf|eot|otf|"
    r"docx?|xlsx?|pptx?|rtf|"
    r"cdf|nc|hdf|h5|fits|fit|ps|eps|"
    r"epub|mobi|glb|gltf|stl|srt|kmz|kml|"
    r"bsp|dat|bin|"
    r"tcl|sh|bat|cmd|csh|pl|py|rb|"
    r"jnlp|class)(\?|$)",
    re.I,
)

PDF_EXT = re.compile(r"\.pdf(\?|$)", re.I)
PLAIN_EXT = re.compile(
    r"\.(txt|text|md|markdown|csv|tsv|json|xml|"
    r"log|bib|ris|tex|sql|yaml|yml)(\?|$)",
    re.I,
)


def path_kind(url: str) -> str:
    path = urlparse(url).path or "/"
    if SKIP_EXT.search(path):
        return "skip"
    if PDF_EXT.search(path):
        return "pdf"
    if re.search(r"\.(html?|htm)(\?|$)", path, re.I):
        return "html"
    if PLAIN_EXT.search(path):
        return "plain"
    return "html"


def response_kind(url: str, content_type: str, body: bytes) -> str:
    kind = path_kind(url)
    if kind == "skip":
        return "skip"

    ctype = (content_type or "").lower()
    head = body[:8] if body else b""

    if kind == "pdf" or "application/pdf" in ctype or head.startswith(b"%PDF"):
        return "pdf"
    if kind == "plain":
        return "plain"
    if ctype.startswith("text/") and "html" not in ctype:
        return "plain"
    if "json" in ctype or "xml" in ctype:
        return "plain"
    return "html"


def extract_page(html: str, metadata: dict | None = None) -> tuple[str, str]:
    if not html:
        return _title_from_metadata(metadata), ""

    soup = BeautifulSoup(html, "lxml")
    title = _title_from_metadata(metadata)
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()

    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    body = soup.body or soup
    text = body.get_text(separator=" ", strip=True)
    full_text = re.sub(r"\s+", " ", text).strip()
    return title, full_text


def pdf_full_text(body: bytes) -> str:
    reader = PdfReader(BytesIO(body))
    chunks = [page.extract_text() or "" for page in reader.pages]
    return re.sub(r"\s+", " ", " ".join(chunks)).strip()


def plain_full_text(body: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return body.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace").strip()


def file_title(url: str) -> str:
    name = urlparse(url).path.rsplit("/", 1)[-1]
    return name or url


def _title_from_metadata(metadata: dict | None) -> str:
    if not metadata:
        return ""
    title = metadata.get("title") or metadata.get("og:title")
    return str(title).strip() if title else ""
