import io
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from pypdf import PdfWriter

from surplus.fetch import fetch, html_text


def _pdf_bytes() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture()
def server():
    pdf = _pdf_bytes()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):
            if self.path.startswith("/forbidden"):
                self.send_response(403); self.end_headers(); return
            if self.path.startswith("/report.pdf"):
                body, ctype = pdf, "application/pdf"
            elif self.path.startswith("/showpublisheddocument/11509"):
                body, ctype = pdf, "application/octet-stream"
            else:
                body = (b"<html><body><h1>Tax Deed Reports</h1><script>x=1</script>"
                        b"<p>Weekly report.</p><a href='/showpublisheddocument/11509'>Tax Deed Surplus Weekly Report</a>"
                        b"<table><tr><td>A</td><td>B</td></tr></table></body></html>")
                ctype = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_html_text_and_links(server, tmp_path):
    f = fetch(server + "/reports", save_dir=tmp_path)
    assert f.kind == "html"
    assert "Tax Deed Reports" in f.text and "x=1" not in f.text and "A | B" in f.text
    assert f.links[0][0] == "Tax Deed Surplus Weekly Report"
    assert f.links[0][1] == server + "/showpublisheddocument/11509"


def test_pdf_by_content_type_and_by_magic_bytes(server, tmp_path):
    a = fetch(server + "/report.pdf", save_dir=tmp_path)
    b = fetch(server + "/showpublisheddocument/11509", save_dir=tmp_path)
    assert a.kind == "pdf" and b.kind == "pdf"
    assert "--- page 1 ---" in b.text
    assert b.saved_to.endswith(".pdf")


def test_fetch_url_tool_pages(env, server):
    from surplus.tools import make_tools
    tools = {t.name: t for t in make_tools(*env)}
    out = tools["fetch_url"].call({"url": server + "/reports", "max_chars": 10})
    assert "more characters" in out and "Links:" in out
    assert "Error" in tools["fetch_url"].call({"url": "http://127.0.0.1:1/nope"})


def test_html_text_helper():
    text, links = html_text(b"<p>Hi &amp; bye</p><a href='x.pdf'>X</a>", "https://e.org/a/")
    assert "Hi & bye" in text and links == [("X", "https://e.org/a/x.pdf")]


def test_ssl_context_uses_os_trust_store():
    import ssl
    from surplus.fetch import ssl_context
    ctx = ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_local_file_path(tmp_path):
    pdf = tmp_path / "list.pdf"
    pdf.write_bytes(_pdf_bytes())
    f = fetch(str(pdf))
    assert f.kind == "pdf" and "--- page 1 ---" in f.text
    f2 = fetch("file://" + str(pdf))
    assert f2.kind == "pdf"


def test_forbidden_falls_through_with_clear_message(server, tmp_path, monkeypatch):
    import surplus.fetch as fm
    monkeypatch.setattr(fm, "_download_curl", lambda u, t: (_ for _ in ()).throw(fm.Forbidden("curl got 403")))
    monkeypatch.setattr(fm, "_download_playwright", lambda u, t: (_ for _ in ()).throw(fm.Forbidden("no browser")))
    with pytest.raises(fm.Forbidden) as exc:
        fetch(server + "/forbidden", save_dir=tmp_path)
    assert "refused scripted access" in str(exc.value) and "playwright" in str(exc.value)


def test_playwright_path_downloads_pdf_from_inside_page(server, tmp_path):
    pytest.importorskip("playwright")
    import surplus.fetch as fm
    data, ctype, final = fm._download_playwright(server + "/showpublisheddocument/11509", timeout=30)
    assert data[:5] == b"%PDF-"
    assert final.endswith("/showpublisheddocument/11509")
    html, ctype2, _ = fm._download_playwright(server + "/reports", timeout=30)
    assert b"Tax Deed Reports" in html
