"""Solve the captcha in a form: free first, the paid API only when needed.

    from webtest.captcha import solve_form_captcha

    result = await solve_form_captcha(page, page.locator("form#contact"))
    # None            -> the form has no captcha
    # result.method   -> "auto"  (passed by itself: Turnstile with a clean browser,
    #                             reCAPTCHA checkbox that did not ask for pictures)
    #                    "api"   (token/text from the solving service, injected)

The token is written where the widget itself would write it (g-recaptcha-response,
h-captcha-response, cf-turnstile-response, the image answer input) and the widget's
callback is called, because many sites only enable the submit button, or submit the
form themselves, from that callback.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Any

from patchright.async_api import Error as PWError
from patchright.async_api import Locator, Page

from webtest.captcha.client import CaptchaClient, CaptchaError
from webtest.captcha.detect import (
    HCAPTCHA,
    IMAGE,
    RECAPTCHA_V2,
    RECAPTCHA_V3,
    TURNSTILE,
    UNSUPPORTED,
    CaptchaInfo,
    detect_captcha,
)


class CaptchaUnavailable(CaptchaError):
    """The captcha needs the API but there is no key, or the type is not supported."""


@dataclass
class CaptchaResult:
    kind: str
    sitekey: str | None
    method: str  # "auto" | "api"
    elapsed_s: float
    task_id: int | str | None = None
    cost: str | None = None
    callback: str | None = None
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        bits = [f"{self.kind} solved via {self.method} in {self.elapsed_s}s"]
        if self.task_id is not None:
            bits.append(f"task {self.task_id}")
        if self.cost:
            bits.append(f"cost {self.cost}")
        if self.callback:
            bits.append(f"callback {self.callback}")
        return ", ".join(bits) + (f" ({'; '.join(self.notes)})" if self.notes else "")


RESPONSE_FIELDS = {
    RECAPTCHA_V2: ["g-recaptcha-response"],
    RECAPTCHA_V3: ["g-recaptcha-response"],
    HCAPTCHA: ["h-captcha-response", "g-recaptcha-response"],
    TURNSTILE: ["cf-turnstile-response"],
}

# Runs in the page's MAIN world: callbacks are the site's own globals.
INJECT_JS = r"""
(root, [kind, token, names, callbackName]) => {
  const form = root.tagName === 'FORM' ? root : (root.closest('form') || root);
  let written = 0;
  for (const name of names) {
    let els = [...document.querySelectorAll(`[name="${name}"]`)];
    if (!els.length) {
      const ta = document.createElement('textarea');
      ta.name = name; ta.style.display = 'none';
      form.appendChild(ta); els = [ta];
    }
    for (const el of els) {
      el.value = token;
      if (el.tagName === 'TEXTAREA') el.innerHTML = token;
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
      written++;
    }
  }
  const lookup = (path) => path.split('.').reduce((o, k) => (o == null ? o : o[k]), window);
  let called = null;
  if (callbackName && typeof lookup(callbackName) === 'function') {
    lookup(callbackName)(token); called = callbackName;
  } else if (kind.startsWith('recaptcha') && window.___grecaptcha_cfg) {
    // widgets rendered with grecaptcha.render({callback}) keep it in this config tree
    const seen = new Set();
    const walk = (o, depth) => {
      if (!o || typeof o !== 'object' || depth > 5 || seen.has(o) || called) return;
      seen.add(o);
      for (const [k, v] of Object.entries(o)) {
        if (k === 'callback' && (typeof v === 'function' || typeof v === 'string')) {
          const fn = typeof v === 'function' ? v : lookup(v);
          if (typeof fn === 'function') { fn(token); called = '___grecaptcha_cfg'; return; }
        }
        walk(v, depth + 1);
      }
    };
    walk(window.___grecaptcha_cfg.clients, 0);
  }
  return {written, called};
}
"""


async def _token_in_page(page: Page, names: list[str]) -> str:
    for name in names:
        loc = page.locator(f'[name="{name}"]')
        for i in range(await loc.count()):
            try:
                value = await loc.nth(i).input_value()
            except PWError:
                continue
            if value:
                return value
    return ""


async def _wait_token(page: Page, names: list[str], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if await _token_in_page(page, names):
            return True
        await page.wait_for_timeout(500)
    return False


async def _try_free(page: Page, info: CaptchaInfo) -> bool:
    """No API spend: Turnstile often passes a real Chrome by itself; a reCAPTCHA/hCaptcha
    checkbox sometimes passes on one click without a picture challenge."""
    names = RESPONSE_FIELDS.get(info.kind, [])
    if await _token_in_page(page, names):
        return True
    if info.kind == TURNSTILE:
        return await _wait_token(page, names, 10)
    checkbox = None
    if info.kind == RECAPTCHA_V2 and not info.invisible:
        checkbox = page.frame_locator("iframe[src*='/recaptcha/'][src*='anchor']").first \
            .locator("#recaptcha-anchor")
    elif info.kind == HCAPTCHA and not info.invisible:
        checkbox = page.frame_locator("iframe[src*='hcaptcha.com'][src*='checkbox']").first \
            .locator("#checkbox")
    if checkbox is None:
        return False
    try:
        await checkbox.click(timeout=5_000)
    except PWError:
        return False
    return await _wait_token(page, names, 6)


async def _user_agent(page: Page) -> str:
    return await page.evaluate("navigator.userAgent")


async def build_task(page: Page, info: CaptchaInfo, *, v3_action: str | None = None,
                     v3_min_score: float = 0.7) -> dict[str, Any]:
    url = page.url
    if info.kind == RECAPTCHA_V2:
        task: dict[str, Any] = {
            "type": "RecaptchaV2EnterpriseTaskProxyless" if info.enterprise
            else "RecaptchaV2TaskProxyless",
            "websiteURL": url, "websiteKey": info.sitekey, "isInvisible": info.invisible,
            "userAgent": await _user_agent(page),
        }
        if info.enterprise and info.data_s:
            task["enterprisePayload"] = {"s": info.data_s}
        return task
    if info.kind == RECAPTCHA_V3:
        return {
            "type": "RecaptchaV3TaskProxyless", "websiteURL": url, "websiteKey": info.sitekey,
            "pageAction": v3_action or info.action or "submit", "minScore": v3_min_score,
            "isEnterprise": info.enterprise,
        }
    if info.kind == HCAPTCHA:
        return {"type": "HCaptchaTaskProxyless", "websiteURL": url, "websiteKey": info.sitekey,
                "isInvisible": info.invisible, "userAgent": await _user_agent(page)}
    if info.kind == TURNSTILE:
        task = {"type": "TurnstileTaskProxyless", "websiteURL": url, "websiteKey": info.sitekey}
        if info.action:
            task["action"] = info.action
        if info.cdata:
            task["data"] = info.cdata
        return task
    if info.kind == IMAGE:
        # screenshot of the element = exactly the pixels a person sees; re-downloading
        # the src would often return a NEW captcha bound to a new session value
        png = await info.image.screenshot()
        return {"type": "ImageToTextTask", "body": base64.b64encode(png).decode()}
    raise CaptchaUnavailable("UNSUPPORTED", f"no task type for {info.kind}")


async def solve_captcha(
    page: Page,
    info: CaptchaInfo,
    client: CaptchaClient | None,
    *,
    form: Locator | None = None,
    try_free: bool = True,
    v3_action: str | None = None,
) -> CaptchaResult:
    started = time.monotonic()
    if info.kind == UNSUPPORTED:
        raise CaptchaUnavailable("UNSUPPORTED", f"{info.vendor} challenge is not supported")

    if try_free and info.kind in (RECAPTCHA_V2, HCAPTCHA, TURNSTILE) and await _try_free(page, info):
        return CaptchaResult(info.kind, info.sitekey, "auto",
                             round(time.monotonic() - started, 1))

    if client is None:
        raise CaptchaUnavailable(
            "NO_KEY", f"{info.kind} needs the solving API: set captcha_key in .env"
        )
    task = await build_task(page, info, v3_action=v3_action)
    solution = await client.solve(task)
    token = solution.token
    notes: list[str] = []
    called = None

    if info.kind == IMAGE:
        await info.answer_input.fill(token)
    else:
        root = form if form is not None else page.locator("body")
        done = await root.evaluate(
            INJECT_JS, [info.kind, token, RESPONSE_FIELDS[info.kind], info.callback],
            isolated_context=False,
        )
        called = done["called"]
        if info.kind == RECAPTCHA_V3:
            notes.append("v3: if the site calls grecaptcha.execute itself on submit, "
                         "it will overwrite this token")
    return CaptchaResult(info.kind, info.sitekey, "api", round(time.monotonic() - started, 1),
                         solution.task_id, solution.cost, called, notes)


async def solve_form_captcha(
    page: Page,
    form: Locator | None = None,
    client: CaptchaClient | None = None,
    *,
    use_env_key: bool = True,
    try_free: bool = True,
    v3_action: str | None = None,
) -> CaptchaResult | None:
    """If the form has a captcha, solve it (free first, then the API). None = no captcha.

    `client` defaults to CaptchaClient.from_env() (captcha_key / CAPTCHA_PROVIDER)."""
    info = await detect_captcha(page, form)
    if info is None:
        return None
    if client is None and use_env_key:
        client = CaptchaClient.from_env()
    return await solve_captcha(page, info, client, form=form, try_free=try_free,
                               v3_action=v3_action)
