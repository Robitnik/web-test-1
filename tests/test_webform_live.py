"""webform end to end against a local server that records real POSTs.
Run: WEBTEST_LIVE=1 uv run pytest -m live"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from webtest.formcli import EXIT_FIELDS, EXIT_INVALID, EXIT_OK, EXIT_PAGE, main

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("WEBTEST_LIVE") != "1", reason="set WEBTEST_LIVE=1"),
]

PAGE = b"""<!doctype html><html><head><title>Contact</title></head><body>
<form id="search" action="/search"><input name="q"><button>Go</button></form>
<form id="contact" action="/submit" method="post" enctype="multipart/form-data">
  <input name="name" required>
  <input name="email" type="email" required>
  <select name="topic"><option value="seo">SEO</option><option value="ads">Ads</option></select>
  <label><input type="checkbox" name="agree" value="yes" required> I agree</label>
  <input type="file" name="attachment">
  <textarea name="message"></textarea>
  <button>Send</button>
</form></body></html>"""


@pytest.fixture
def server():
    posts = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGE if self.path == "/" else b"<title>Thanks</title>Thanks"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            posts.append((self.path, body))
            self.send_response(303)
            self.send_header("Location", "/thanks")
            self.end_headers()

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/", posts
    srv.shutdown()


def _args(tmp_path, url, form, data):
    return [url, form, data, "--headless", "--profile", str(tmp_path / "profile"),
            "--out", str(tmp_path / "out"), "--settle-ms", "1000"]


def test_submits_fields_and_file_from_json_file(tmp_path, server):
    url, posts = server
    (tmp_path / "doc.txt").write_text("attached body")
    data = tmp_path / "data.json"
    data.write_text(json.dumps({
        "name": "Olena", "email": "olena@example.com", "topic": "Ads",
        "agree": True, "attachment": "doc.txt", "message": "Hi",
    }))
    assert main(_args(tmp_path, url, "#contact", str(data))) == EXIT_OK
    assert len(posts) == 1
    path, body = posts[0]
    assert path == "/submit"
    assert b'name="name"\r\n\r\nOlena' in body
    assert b'name="topic"\r\n\r\nads' in body
    assert b'filename="doc.txt"' in body and b"attached body" in body
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert report["url_after"].endswith("/thanks")
    assert [r["method"] for r in report["requests"]][:1] == ["POST"]


def test_browser_validation_blocks_before_click(tmp_path, server):
    url, posts = server
    code = main(_args(tmp_path, url, "#contact", '{"name": "Olena", "email": "not-an-email"}'))
    assert code == EXIT_INVALID
    assert posts == []


def test_unknown_field_and_missing_form(tmp_path, server):
    url, posts = server
    assert main(_args(tmp_path, url, "#contact", '{"nope": "x"}') + ["--timeout", "500"]) \
        == EXIT_FIELDS
    assert main(_args(tmp_path, url, "#missing", '{"name": "x"}') + ["--timeout", "500"]) \
        == EXIT_PAGE
    assert posts == []
