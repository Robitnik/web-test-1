"""Launches a real browser. Run: WEBTEST_LIVE=1 uv run pytest -m live"""

import asyncio
import os

import pytest

from webtest import Browser, BrowserConfig
from webtest.diagnostics import fingerprint, red_flags

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("WEBTEST_LIVE") != "1", reason="set WEBTEST_LIVE=1"),
]


def test_launch_and_fingerprint(tmp_path):
    cfg = BrowserConfig(
        profile_dir=tmp_path / "profile",
        downloads_dir=tmp_path / "dl",
        headless=os.environ.get("WEBTEST_HEADLESS", "1") == "1",
    )

    async def run():
        async with Browser(cfg) as b:
            page = await b.page()
            status = await b.goto(page, "https://example.com/", settle_ms=2000)
            # main world sees page globals, isolated world does not
            await b.eval_main(page, "window.__probe = 42")
            assert await b.eval_main(page, "window.__probe") == 42
            assert await b.eval_isolated(page, "window.__probe ?? null") is None
            return status, await fingerprint(page)

    status, fp = asyncio.run(run())
    assert status == 200
    assert fp["webdriver"] is False
    if not cfg.headless:
        assert red_flags(fp) == []
