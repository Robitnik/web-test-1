"""Captcha-solving API client (createTask / getTaskResult protocol).

2captcha (API v2), anti-captcha and CapMonster Cloud speak the same JSON protocol, so
one client covers all three; only the base URL differs.

    client = CaptchaClient.from_env()          # captcha_key / CAPTCHA_KEY, CAPTCHA_PROVIDER
    print(await client.balance())
    sol = await client.solve({"type": "TurnstileTaskProxyless",
                              "websiteURL": url, "websiteKey": sitekey})
    sol.token

The key never appears in errors, logs or repr.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

PROVIDERS = {
    "2captcha": "https://api.2captcha.com",
    "anticaptcha": "https://api.anti-captcha.com",
    "capmonster": "https://api.capmonster.cloud",
}

# per provider: method used to report a wrong answer, by task family
_REPORT = {
    "2captcha": {"*": "reportIncorrect"},
    "anticaptcha": {"recaptcha": "reportIncorrectRecaptcha", "image": "reportIncorrectImageCaptcha"},
    "capmonster": {},
}

Transport = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class CaptchaError(RuntimeError):
    def __init__(self, code: str, description: str = ""):
        self.code = code
        self.description = description
        super().__init__(f"{code}: {description}" if description else code)


class CaptchaTimeout(CaptchaError):
    pass


@dataclass
class Solution:
    task_id: int | str
    solution: dict[str, Any]
    elapsed_s: float
    cost: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def token(self) -> str:
        """The value to put into the page: token for widgets, text for image captchas."""
        s = self.solution
        value = s.get("gRecaptchaResponse") or s.get("token") or s.get("text")
        if not value:
            raise CaptchaError("EMPTY_SOLUTION", f"no token/text in solution keys {sorted(s)}")
        return str(value)


async def _urllib_transport(url: str, body: dict[str, Any]) -> dict[str, Any]:
    def call() -> dict[str, Any]:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            raise CaptchaError(f"HTTP_{exc.code}", exc.reason) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise CaptchaError("NETWORK", str(getattr(exc, "reason", exc))) from None

    return await asyncio.to_thread(call)


def _env_key(env: Mapping[str, str]) -> str | None:
    for name in ("CAPTCHA_KEY", "captcha_key"):
        value = (env.get(name) or "").strip().strip("\"'")
        if value:
            return value
    return None


class CaptchaClient:
    def __init__(
        self,
        key: str,
        provider: str = "2captcha",
        *,
        timeout_s: float = 180,
        poll_s: float = 5,
        transport: Transport | None = None,
    ) -> None:
        provider = provider.lower().replace("-", "").replace("_", "")
        if provider not in PROVIDERS:
            raise ValueError(f"unknown captcha provider {provider!r}; one of {sorted(PROVIDERS)}")
        if not key:
            raise ValueError("captcha key is empty")
        self._key = key
        self.provider = provider
        self.base_url = PROVIDERS[provider]
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self._transport = transport or _urllib_transport

    def __repr__(self) -> str:
        return f"CaptchaClient(provider={self.provider!r}, key=***{self._key[-2:]})"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **kw: Any) -> CaptchaClient | None:
        """None when no key is configured (the caller decides whether that is an error)."""
        if env is None:
            from dotenv import load_dotenv

            load_dotenv()
            env = os.environ
        key = _env_key(env)
        if not key:
            return None
        provider = (env.get("CAPTCHA_PROVIDER") or "2captcha").strip()
        timeout = float(env.get("CAPTCHA_TIMEOUT_S") or kw.pop("timeout_s", 180))
        return cls(key, provider, timeout_s=timeout, **kw)

    # ------------------------------------------------------------------ protocol
    async def _call(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = {"clientKey": self._key, **(payload or {})}
        resp = await self._transport(f"{self.base_url}/{method}", body)
        if resp.get("errorId"):
            raise CaptchaError(str(resp.get("errorCode") or "ERROR"),
                               str(resp.get("errorDescription") or ""))
        return resp

    async def balance(self) -> float:
        return float((await self._call("getBalance"))["balance"])

    async def create_task(self, task: dict[str, Any]) -> int | str:
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                return (await self._call("createTask", {"task": task}))["taskId"]
            except CaptchaError as exc:
                if exc.code != "ERROR_NO_SLOT_AVAILABLE" or time.monotonic() > deadline:
                    raise
            await asyncio.sleep(self.poll_s)

    async def task_result(self, task_id: int | str) -> dict[str, Any]:
        return await self._call("getTaskResult", {"taskId": task_id})

    async def solve(self, task: dict[str, Any], *, first_poll_s: float | None = None) -> Solution:
        """Create a task and poll until it is ready. Image tasks are fast, token tasks
        take 10-60 s, hence the longer first wait for them."""
        started = time.monotonic()
        task_id = await self.create_task(task)
        log.info("captcha task %s created (%s)", task_id, task.get("type"))
        if first_poll_s is None:
            first_poll_s = 3 if task.get("type") == "ImageToTextTask" else 10
        await asyncio.sleep(first_poll_s)
        while True:
            resp = await self.task_result(task_id)
            if resp.get("status") == "ready":
                return Solution(task_id, resp.get("solution") or {},
                                round(time.monotonic() - started, 1), resp.get("cost"), resp)
            if time.monotonic() - started > self.timeout_s:
                raise CaptchaTimeout("TIMEOUT", f"task {task_id} not ready in {self.timeout_s}s")
            await asyncio.sleep(self.poll_s)

    async def report_incorrect(self, task_id: int | str, family: str) -> bool:
        """Tell the provider the answer was rejected by the site (refund on most plans)."""
        methods = _REPORT[self.provider]
        method = methods.get(family) or methods.get("*")
        if not method:
            return False
        await self._call(method, {"taskId": task_id})
        return True
