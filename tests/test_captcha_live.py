"""Captcha flow in a real browser, WITHOUT spending the API key.

* Free path: Google's and Cloudflare's official TEST sitekeys always pass, so the
  "auto" route (click the checkbox / wait for Turnstile) is exercised for real.
* API path: a fake client returns a token; we check it lands in the response field
  and the widget callback runs.
Run: WEBTEST_LIVE=1 uv run pytest -m live
"""

import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

from webtest import Browser, BrowserConfig
from webtest.captcha import Solution, detect_captcha, solve_form_captcha
from webtest.formcli import EXIT_CAPTCHA, EXIT_OK, main

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("WEBTEST_LIVE") != "1", reason="set WEBTEST_LIVE=1"),
]

RECAPTCHA_TEST_KEY = "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"
TURNSTILE_PASS_KEY = "1x00000000000000000000AA"
TINY_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
            "AAAABJRU5ErkJggg==")

PAGES = {
    "/recaptcha": f"""<script src="https://www.google.com/recaptcha/api.js" async defer></script>
<form id="f" method="post" action="/submit"><input name="name">
<div class="g-recaptcha" data-sitekey="{RECAPTCHA_TEST_KEY}"></div><button>Send</button></form>""",
    "/turnstile": f"""<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
<form id="f" method="post" action="/submit"><input name="name">
<div class="cf-turnstile" data-sitekey="{TURNSTILE_PASS_KEY}"></div><button>Send</button></form>""",
    # markup only, no widget script: nothing can pass for free -> API path
    "/callback": """<form id="f" method="post" action="/submit"><input name="name">
<div class="cf-turnstile" data-sitekey="0x4AAAAAAABkMYinukE8nzY" data-callback="onPass"></div>
<button>Send</button></form>
<script>window.onPass = (t) => { window.__cb = t; };</script>""",
    "/image": f"""<form id="f" method="post" action="/submit"><input name="name">
<img id="captcha_img" src="data:image/png;base64,{TINY_PNG}" style="width:120px;height:40px">
<input name="captcha_answer"><button>Send</button></form>""",
    "/plain": """<form id="f" method="post" action="/submit"><input name="name"><button>Send</button></form>""",
}


@pytest.fixture
def server():
    posts = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGES.get(self.path, "<title>Thanks</title>ok")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<!doctype html><html><body>{body}</body></html>".encode())

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
            posts.append(parse_qs(raw))
            self.send_response(303)
            self.send_header("Location", "/thanks")
            self.end_headers()

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # Google's test key is bound to "localhost", not 127.0.0.1
    yield f"http://localhost:{srv.server_port}", posts
    srv.shutdown()


class FakeClient:
    def __init__(self, token):
        self.token, self.tasks = token, []

    async def solve(self, task, **kw):
        self.tasks.append(task)
        return Solution("fake-1", {"token": self.token, "text": self.token}, 0.0, "0.001")


def in_browser(tmp_path, url, scenario):
    cfg = BrowserConfig(profile_dir=tmp_path / "profile", downloads_dir=tmp_path / "dl",
                        headless=os.environ.get("WEBTEST_HEADLESS", "1") == "1")

    async def main_():
        async with Browser(cfg) as b:
            page = await b.page()
            await b.goto(page, url, settle_ms=2_000)
            return await scenario(b, page)

    return asyncio.run(main_())


def webform(tmp_path, url, data='{"name": "Olena"}', *extra):
    return main([url, "#f", data, "--headless", "--profile", str(tmp_path / "p"),
                 "--out", str(tmp_path / "out"), "--settle-ms", "1000", *extra])


def test_no_captcha(tmp_path, server):
    base, _ = server
    assert in_browser(tmp_path, base + "/plain",
                      lambda b, p: solve_form_captcha(p, p.locator("#f"), use_env_key=False)) is None


def test_recaptcha_checkbox_passes_free_and_submits(tmp_path, server):
    base, posts = server
    assert webform(tmp_path, base + "/recaptcha", '{"name": "Olena"}', "--captcha", "free") \
        == EXIT_OK
    assert posts and posts[0]["name"] == ["Olena"]
    assert posts[0]["g-recaptcha-response"][0]  # real token from Google's test widget


def test_turnstile_passes_free_and_submits(tmp_path, server):
    base, posts = server
    assert webform(tmp_path, base + "/turnstile", '{"name": "Olena"}', "--captcha", "free") \
        == EXIT_OK
    assert posts and posts[0]["cf-turnstile-response"][0]


def test_api_token_injected_and_callback_called(tmp_path, server):
    base, _ = server
    fake = FakeClient("API-TOKEN")

    async def scenario(b, page):
        res = await solve_form_captcha(page, page.locator("#f"), fake)
        value = await page.locator('[name="cf-turnstile-response"]').input_value()
        return res, value, await b.eval_main(page, "window.__cb")

    res, value, cb = in_browser(tmp_path, base + "/callback", scenario)
    assert res.method == "api" and res.callback == "onPass"
    assert value == "API-TOKEN" and cb == "API-TOKEN"
    assert fake.tasks[0]["type"] == "TurnstileTaskProxyless"
    assert fake.tasks[0]["websiteKey"] == "0x4AAAAAAABkMYinukE8nzY"


def test_image_captcha_via_api(tmp_path, server):
    base, _ = server
    fake = FakeClient("x7k2p")

    async def scenario(b, page):
        info = await detect_captcha(page, page.locator("#f"))
        res = await solve_form_captcha(page, page.locator("#f"), fake)
        return info.kind, res, await page.locator('[name="captcha_answer"]').input_value()

    kind, res, answer = in_browser(tmp_path, base + "/image", scenario)
    assert kind == "image" and res.method == "api" and answer == "x7k2p"
    assert fake.tasks[0]["type"] == "ImageToTextTask" and fake.tasks[0]["body"]


def test_needs_api_but_free_only_stops_before_submit(tmp_path, server):
    base, posts = server
    assert webform(tmp_path, base + "/callback", '{"name": "Olena"}', "--captcha", "free") \
        == EXIT_CAPTCHA
    assert posts == []
