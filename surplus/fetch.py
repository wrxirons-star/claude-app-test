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
import urllib.error
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


BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class Forbidden(Exception):
    pass


def _download_urllib(url: str, timeout: int) -> tuple[bytes, str, str]:
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
            data = resp.read(MAX_BYTES + 1)
            return data, resp.headers.get("Content-Type", ""), resp.geturl()
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 406, 429, 503):
            raise Forbidden(f"HTTP {exc.code}") from exc
        raise


def _download_curl(url: str, timeout: int) -> tuple[bytes, str, str]:
    import shutil
    import subprocess
    import tempfile

    curl = shutil.which("curl")
    if not curl:
        raise Forbidden("curl not available")
    with tempfile.TemporaryDirectory() as td:
        hdr = Path(td) / "h"
        args = [curl, "-sSL", "--max-time", str(timeout), "-D", str(hdr), "-o", "-", "--compressed",
                "-w", "\n%{url_effective}"]
        for k, v in BROWSER_HEADERS.items():
            args += ["-H", f"{k}: {v}"]
        args.append(url)
        proc = subprocess.run(args, capture_output=True, timeout=timeout + 10)
        if proc.returncode != 0:
            raise Forbidden(f"curl failed: {proc.stderr.decode(errors='replace')[:200]}")
        body, _, effective = proc.stdout.rpartition(b"\n")
        headers = hdr.read_text(errors="replace")
        status = [l for l in headers.splitlines() if l.startswith("HTTP/")]
        if status and status[-1].split()[1] in ("403", "406", "429", "503"):
            raise Forbidden(f"curl got {status[-1]}")
        ctype = ""
        for line in headers.splitlines():
            if line.lower().startswith("content-type:"):
                ctype = line.split(":", 1)[1].strip()
        return body, ctype, effective.decode(errors="replace").strip() or url


_PAGE_FETCH_JS = """async (u) => {
  const r = await fetch(u, {credentials: 'include', redirect: 'follow'});
  const buf = new Uint8Array(await r.arrayBuffer());
  let bin = '';
  for (let i = 0; i < buf.length; i += 0x8000) {
    bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  }
  return {status: r.status, ctype: r.headers.get('content-type') || '', url: r.url, data: btoa(bin)};
}"""

CHALLENGE_TITLES = ("just a moment", "attention required", "access denied", "checking your browser",
                    "verify you are human", "one moment")


def _download_playwright(url: str, timeout: int) -> tuple[bytes, str, str]:
    """Drive a real Chrome tab. The request for the target file is issued from
    inside the page with fetch(), so it carries Chrome's TLS/HTTP fingerprint
    and any challenge cookies the site set on the landing page."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise Forbidden("playwright not installed (pip install playwright && playwright install chromium)")
    import base64
    from urllib.parse import urlsplit

    root = "{0.scheme}://{0.netloc}/".format(urlsplit(url))
    with sync_playwright() as p:
        import os
        launch = dict(headless=True, args=["--disable-blink-features=AutomationControlled"])
        exe = os.environ.get("SURPLUS_CHROME_PATH")  # override for unusual installs
        if exe:
            browser = p.chromium.launch(executable_path=exe, **launch)
        else:
            try:
                browser = p.chromium.launch(channel="chromium", **launch)  # full Chrome, new headless mode
            except Exception:
                browser = p.chromium.launch(**launch)
        ctx = browser.new_context(user_agent=USER_AGENT, locale="en-US", viewport={"width": 1366, "height": 900})
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = ctx.new_page()
        try:
            page.goto(root, wait_until="domcontentloaded", timeout=timeout * 1000)
            for _ in range(12):  # give a bot-check interstitial time to clear
                if not any(t in (page.title() or "").lower() for t in CHALLENGE_TITLES):
                    break
                page.wait_for_timeout(1500)
            page.wait_for_timeout(1000)
            res = page.evaluate(_PAGE_FETCH_JS, url)
            if res["status"] in (403, 406, 429, 503):
                # Last resort: navigate to it directly and take the rendered HTML.
                resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                page.wait_for_timeout(1500)
                if resp is None or resp.status in (403, 406, 429, 503):
                    raise Forbidden(f"browser got HTTP {res['status']}")
                return page.content().encode(), resp.headers.get("content-type", "text/html"), page.url
            return base64.b64decode(res["data"]), res["ctype"], res["url"]
        finally:
            browser.close()


def _download(url: str, timeout: int = 60) -> tuple[bytes, str, str]:
    """Local file, then urllib, then curl, then a headless browser."""
    if url.startswith("file://") or (not url.startswith("http") and Path(url).expanduser().exists()):
        path = Path(url[7:] if url.startswith("file://") else url).expanduser()
        data = path.read_bytes()
        ctype = "application/pdf" if path.suffix.lower() == ".pdf" else "text/html" if path.suffix.lower() in (".htm", ".html") else "text/plain"
        return data, ctype, str(path)
    errors = []
    for fn in (_download_urllib, _download_curl, _download_playwright):
        try:
            data, ctype, final = fn(url, timeout)
            if len(data) > MAX_BYTES:
                raise ValueError(f"Document larger than {MAX_BYTES // (1024 * 1024)} MB")
            return data, ctype, final
        except Forbidden as exc:
            errors.append(f"{fn.__name__}: {exc}")
    raise Forbidden("Site refused scripted access. Tried " + "; ".join(errors) +
                    ". Install a headless browser (pip install playwright && playwright install chromium), "
                    "or open the URL in your browser, save the file, and pass its path to fetch instead.")


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
