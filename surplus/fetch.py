"""Client-side document fetcher.

Anthropic's server-side web_fetch cannot open many county attachments (PDF
reports behind showpublisheddocument links, RealTDM portals). This module
downloads from the operator's own machine and extracts text, so the agent can
read the same files a person can open in a browser.
"""
from __future__ import annotations

import html
import io
import re
import ssl
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MAX_BYTES = 40 * 1024 * 1024


@dataclass
class Fetched:
    url: str
    final_url: str
    content_type: str
    kind: str  # pdf | html | text | binary
    text: str
    links: list[tuple[str, str]] = field(default_factory=list)  # (label, absolute url)
    saved_to: str | None = None


class _TextAndLinks(HTMLParser):
    def __init__(self, base: str):
        super().__init__()
        self.base = base
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._label: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag == "a":
            href = dict(attrs).get("href")
            self._href = urljoin(self.base, href) if href else None
            self._label = []
        if tag in ("p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table"):
            self.parts.append("\n")
        if tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
        if tag == "a" and self._href:
            label = " ".join("".join(self._label).split())
            self.links.append((label, self._href))
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._label.append(data)


def ssl_context() -> ssl.SSLContext:
    """Use the operating system's trust store. python.org's macOS installer ships
    Python without root certificates, so the default context fails on every
    HTTPS site until the user runs 'Install Certificates.command'. truststore
    (a dependency of the Anthropic SDK) reads the OS keychain instead; certifi
    is the fallback."""
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        pass
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _download(url: str, timeout: int = 60) -> tuple[bytes, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
        data = resp.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError(f"Document larger than {MAX_BYTES // (1024 * 1024)} MB")
        return data, resp.headers.get("Content-Type", ""), resp.geturl()


def pdf_text(data: bytes, max_pages: int = 200) -> str:
    from pypdf import PdfReader  # imported lazily; only needed for PDFs

    reader = PdfReader(io.BytesIO(data))
    out = []
    for i, page in enumerate(reader.pages[:max_pages]):
        out.append(f"\n--- page {i + 1} ---\n")
        out.append(page.extract_text() or "")
    if len(reader.pages) > max_pages:
        out.append(f"\n[truncated: {len(reader.pages) - max_pages} more pages]")
    return "".join(out)


def html_text(data: bytes, base: str) -> tuple[str, list[tuple[str, str]]]:
    parser = _TextAndLinks(base)
    parser.feed(data.decode("utf-8", errors="replace"))
    text = html.unescape("".join(parser.parts))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip(), parser.links


def fetch(url: str, save_dir: Path | None = None) -> Fetched:
    data, ctype, final_url = _download(url)
    ctype_l = ctype.lower()
    is_pdf = "pdf" in ctype_l or data[:5] == b"%PDF-"
    saved = None
    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", final_url.split("//", 1)[-1])[:120]
        if is_pdf and not name.lower().endswith(".pdf"):
            name += ".pdf"
        path = save_dir / name
        path.write_bytes(data)
        saved = str(path)
    if is_pdf:
        return Fetched(url, final_url, ctype, "pdf", pdf_text(data), [], saved)
    if "html" in ctype_l or data.lstrip()[:1] == b"<":
        text, links = html_text(data, final_url)
        return Fetched(url, final_url, ctype, "html", text, links, saved)
    if "text" in ctype_l or "csv" in ctype_l or "json" in ctype_l:
        return Fetched(url, final_url, ctype, "text", data.decode("utf-8", errors="replace"), [], saved)
    return Fetched(url, final_url, ctype, "binary", f"[binary content, {len(data)} bytes, type {ctype}]", [], saved)
