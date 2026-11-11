"""Find a captcha in a form (or, failing that, on its page).

Search order: widget containers with data-sitekey inside the form, widget iframes and
image captchas inside the form, then the same page-wide (invisible reCAPTCHA puts its
badge iframe at the end of <body>, and programmatic widgets may sit outside the form).
reCAPTCHA v3 has no widget at all: it is found by its script (?render=<sitekey>).

Iframes are located with patchright locators, which also pierce closed shadow roots
(Cloudflare Turnstile renders inside one).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from patchright.async_api import Error as PWError
from patchright.async_api import Frame, Locator, Page

RECAPTCHA_V2 = "recaptcha_v2"
RECAPTCHA_V3 = "recaptcha_v3"
HCAPTCHA = "hcaptcha"
TURNSTILE = "turnstile"
IMAGE = "image"
UNSUPPORTED = "unsupported"

_UNSUPPORTED_IFRAMES = {
    "arkoselabs": "iframe[src*='arkoselabs.com'], iframe[src*='funcaptcha.com']",
    "geetest": "iframe[src*='geetest.com']",
    "datadome": "iframe[src*='captcha-delivery.com']",
}


@dataclass
class CaptchaInfo:
    kind: str
    sitekey: str | None = None
    invisible: bool = False
    enterprise: bool = False
    action: str | None = None  # reCAPTCHA v3 / Turnstile action
    cdata: str | None = None  # Turnstile cData
    data_s: str | None = None  # reCAPTCHA enterprise "s" value
    callback: str | None = None  # data-callback global function name
    scope: str = "form"  # "form" or "page"
    vendor: str | None = None  # for UNSUPPORTED
    widget: Locator | None = field(default=None, repr=False)
    image: Locator | None = field(default=None, repr=False)
    answer_input: Locator | None = field(default=None, repr=False)


def kind_from_sitekey(sitekey: str) -> str | None:
    if re.fullmatch(r"6L[\w-]{38}", sitekey):
        return RECAPTCHA_V2
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", sitekey):
        return HCAPTCHA
    if re.fullmatch(r"[0-3]x[\w-]{10,}", sitekey):
        return TURNSTILE
    return None


def _kind_from_class(cls: str) -> str | None:
    cls = cls or ""
    if "g-recaptcha" in cls:
        return RECAPTCHA_V2
    if "h-captcha" in cls:
        return HCAPTCHA
    if "cf-turnstile" in cls:
        return TURNSTILE
    return None


def sitekey_from_iframe_src(src: str) -> tuple[str, str, bool] | None:
    """(kind, sitekey, enterprise) from a widget iframe URL."""
    parts = urlsplit(src)
    host, path = parts.netloc, parts.path
    q = {k: v[0] for k, v in parse_qs(parts.query + "&" + parts.fragment).items()}
    if ("google.com" in host or "recaptcha.net" in host) and "/recaptcha/" in path \
            and "anchor" in path and q.get("k"):
        return RECAPTCHA_V2, q["k"], "/enterprise/" in path
    if "hcaptcha.com" in host and q.get("sitekey"):
        return HCAPTCHA, q["sitekey"], False
    if "challenges.cloudflare.com" in host:
        m = re.search(r"/([0-3]x[\w-]{10,})/", path + "/")
        if m:
            return TURNSTILE, m.group(1), False
    return None


async def _attr(loc: Locator, name: str) -> str | None:
    try:
        return await loc.get_attribute(name)
    except PWError:
        return None


async def _from_containers(scope: Page | Frame | Locator, where: str) -> CaptchaInfo | None:
    loc = scope.locator("[data-sitekey]")
    for i in range(await loc.count()):
        el = loc.nth(i)
        sitekey = await _attr(el, "data-sitekey") or ""
        kind = _kind_from_class(await _attr(el, "class") or "") or kind_from_sitekey(sitekey)
        if not kind:
            continue
        return CaptchaInfo(
            kind=kind,
            sitekey=sitekey,
            invisible=(await _attr(el, "data-size")) == "invisible",
            action=await _attr(el, "data-action"),
            cdata=await _attr(el, "data-cdata"),
            data_s=await _attr(el, "data-s"),
            callback=await _attr(el, "data-callback"),
            scope=where,
            widget=el,
        )
    return None


async def _from_iframes(scope: Page | Frame | Locator, where: str) -> CaptchaInfo | None:
    loc = scope.locator(
        "iframe[src*='/recaptcha/'][src*='anchor'], iframe[src*='hcaptcha.com'], "
        "iframe[src*='challenges.cloudflare.com']"
    )
    for i in range(await loc.count()):
        src = await _attr(loc.nth(i), "src") or ""
        found = sitekey_from_iframe_src(src)
        if found:
            kind, sitekey, enterprise = found
            invisible = kind == RECAPTCHA_V2 and "size=invisible" in src
            return CaptchaInfo(kind=kind, sitekey=sitekey, enterprise=enterprise,
                               invisible=invisible, scope=where)
    return None


async def _image_captcha(scope: Page | Frame | Locator, where: str) -> CaptchaInfo | None:
    answer = scope.locator(
        "input[name*='captcha' i]:not([type=hidden]), input[id*='captcha' i]:not([type=hidden]), "
        "input[placeholder*='captcha' i]"
    )
    image = scope.locator(
        "img[src*='captcha' i], img[id*='captcha' i], img[class*='captcha' i], img[alt*='captcha' i]"
    )
    if await answer.count() and await image.count():
        return CaptchaInfo(kind=IMAGE, scope=where, image=image.first, answer_input=answer.first)
    return None


async def _v3_script(page: Page) -> CaptchaInfo | None:
    scripts = page.locator("script[src*='recaptcha/api.js?'], script[src*='recaptcha/enterprise.js?']")
    for i in range(await scripts.count()):
        src = await _attr(scripts.nth(i), "src") or ""
        render = parse_qs(urlsplit(src).query).get("render", [""])[0]
        if render and render not in ("explicit", "onload"):
            return CaptchaInfo(kind=RECAPTCHA_V3, sitekey=render, scope="page",
                               enterprise="enterprise.js" in src)
    return None


async def _unsupported(scope: Page | Frame | Locator, where: str) -> CaptchaInfo | None:
    for vendor, selector in _UNSUPPORTED_IFRAMES.items():
        if await scope.locator(selector).count():
            return CaptchaInfo(kind=UNSUPPORTED, vendor=vendor, scope=where)
    return None


async def detect_captcha(page: Page, form: Locator | None = None) -> CaptchaInfo | None:
    """The captcha guarding `form` (or the page when form is None), or None."""
    scopes: list[tuple[Page | Frame | Locator, str]] = []
    if form is not None:
        scopes.append((form, "form"))
    scopes.append((page, "page"))
    for scope, where in scopes:
        for finder in (_from_containers, _from_iframes, _image_captcha, _unsupported):
            found = await finder(scope, where)
            if found:
                return found
    return await _v3_script(page)
