"""Browser lifecycle and the basic control surface.

Stealth rules this module follows (from the patchright README and our own runs):

* `launch_persistent_context` with a real profile dir, never a throwaway context;
* real Google Chrome (`executable_path`), not the bundled Chromium;
* `no_viewport=True`: the window keeps its real size, so screen and viewport agree;
* no custom `user_agent` and no `extra_http_headers`: they create mismatches with
  Client Hints and the TLS fingerprint and are themselves a detection signal;
* headed by default; headless is the easier one to detect.

patchright runs `page.evaluate` in an ISOLATED world by default, so page globals
(`window.jQuery`, `window.wp`, app state) are invisible there. Use `eval_main()`
when you need the page's own JavaScript world.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Self

from patchright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from patchright.async_api import (
    TimeoutError as PWTimeout,
)

from webtest.chrome import find_chrome
from webtest.config import BrowserConfig
from webtest.profile import ProfileInUse, assert_free

log = logging.getLogger(__name__)

__all__ = ["Browser", "ProfileInUse"]


class Browser:
    """Async context manager around one persistent Chrome profile.

        async with Browser(BrowserConfig.from_env()) as b:
            page = await b.page()
            await b.goto(page, "https://example.com")
            await b.screenshot(page, "out/example.png")
    """

    def __init__(self, config: BrowserConfig | None = None) -> None:
        self.config = config or BrowserConfig.from_env()
        self._pw: Playwright | None = None
        self._ctx: BrowserContext | None = None
        self.chrome_path: Path | None = None

    # ---------------------------------------------------------------- lifecycle
    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def context(self) -> BrowserContext:
        if self._ctx is None:
            raise RuntimeError("browser is not started")
        return self._ctx

    async def start(self) -> None:
        cfg = self.config
        cfg.profile_dir.mkdir(parents=True, exist_ok=True)
        cfg.downloads_dir.mkdir(parents=True, exist_ok=True)
        assert_free(cfg.profile_dir)

        self.chrome_path = find_chrome(cfg.chrome_path)
        kwargs: dict[str, Any] = {
            "user_data_dir": str(cfg.profile_dir.resolve()),
            "headless": cfg.headless,
            "no_viewport": True,
            "accept_downloads": True,
            "downloads_path": str(cfg.downloads_dir.resolve()),
            "slow_mo": cfg.slow_mo_ms or None,
        }
        if self.chrome_path is not None:
            kwargs["executable_path"] = str(self.chrome_path)
        else:
            log.warning(
                "Google Chrome not found, falling back to patchright's bundled Chromium "
                "(more detectable). Run scripts/install_chrome.sh."
            )
        if cfg.proxy:
            kwargs["proxy"] = _proxy(cfg.proxy)
        if cfg.locale:
            kwargs["locale"] = cfg.locale
        if cfg.timezone_id:
            kwargs["timezone_id"] = cfg.timezone_id

        self._pw = await async_playwright().start()
        try:
            self._ctx = await self._pw.chromium.launch_persistent_context(
                **{k: v for k, v in kwargs.items() if v is not None}
            )
        except BaseException:
            await self._pw.stop()
            self._pw = None
            raise
        self._ctx.set_default_timeout(cfg.timeout_ms)
        self._ctx.set_default_navigation_timeout(cfg.timeout_ms)

    async def close(self) -> None:
        if self._ctx is not None:
            try:
                await self._ctx.close()
            finally:
                self._ctx = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    # ------------------------------------------------------------------- pages
    async def page(self) -> Page:
        """The first open tab (a persistent context starts with one), or a new one."""
        pages = self.context.pages
        return pages[0] if pages else await self.context.new_page()

    async def new_page(self) -> Page:
        return await self.context.new_page()

    async def goto(
        self, page: Page, url: str, *, settle_ms: int = 5_000, wait_until: str = "domcontentloaded"
    ) -> int | None:
        """Navigate, then give lazy content up to `settle_ms` to finish. Returns the HTTP status
        of the final response (after redirects), or None for non-HTTP navigations."""
        response = await page.goto(url, wait_until=wait_until)
        await self.settle(page, settle_ms)
        return response.status if response is not None else None

    async def settle(self, page: Page, timeout_ms: int = 5_000) -> None:
        """Best effort network idle. Sites with long polling never go idle; that is fine."""
        if timeout_ms <= 0:
            return
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except PWTimeout:
            pass

    async def screenshot(self, page: Page, path: str | Path, *, full_page: bool = True) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=full_page)
        return path

    async def eval_main(self, page: Page, expression: str, arg: Any = None) -> Any:
        """Evaluate in the page's MAIN world (sees the site's own globals)."""
        return await page.evaluate(expression, arg, isolated_context=False)

    async def eval_isolated(self, page: Page, expression: str, arg: Any = None) -> Any:
        """Evaluate in an isolated world (invisible to the site's scripts). The default."""
        return await page.evaluate(expression, arg)

    async def human_pause(self, min_ms: int = 300, max_ms: int = 900) -> None:
        """Jittered pause between actions; constant-interval clicking is a pattern."""
        import random

        await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)


def _proxy(raw: str) -> dict[str, str]:
    """`http://user:pass@host:port` -> playwright proxy dict (credentials split out)."""
    from urllib.parse import urlsplit

    parts = urlsplit(raw)
    server = f"{parts.scheme or 'http'}://{parts.hostname}"
    if parts.port:
        server += f":{parts.port}"
    proxy = {"server": server}
    if parts.username:
        proxy["username"] = parts.username
    if parts.password:
        proxy["password"] = parts.password
    return proxy
