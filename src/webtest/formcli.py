"""webform: open a URL, fill one form from JSON, submit it, and show what was sent.

    webform URL FORM_SELECTOR DATA [options]

    webform https://site/contact "form#contact" data.json
    webform https://site/contact "form#contact" '{"email": "a@b.c", "cv": "cv.pdf"}'
    cat data.json | webform https://site/contact "#contact" -

DATA is inline JSON, a path to a .json file, or "-" for stdin. It is an object
{field: value}; fields are filled in that order. A key is the field's name attribute
(label, id, placeholder and CSS selectors work too). For a file field the value is a
path or a list of paths; relative paths are looked up in the current directory, then
next to the JSON file.

Before submitting, the browser's own validation (required, type=email, pattern...) is
checked: a form the browser would refuse is reported field by field, not clicked.
After submitting, every POST/PUT/PATCH/DELETE and page navigation is recorded, so the
output shows whether a request actually left the page and what the server answered.

A captcha in the form is detected after filling and passed right before submitting:
for free when the browser passes it by itself (Turnstile, a reCAPTCHA checkbox without
pictures), otherwise through the solving API (captcha_key + CAPTCHA_PROVIDER in .env).

Exit codes: 0 sent (or filled, with --dry-run); 1 a field was not found or did not take
its value; 2 browser validation blocks the submit; 3 page or form not available;
4 the captcha could not be passed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from patchright.async_api import Error as PWError
from patchright.async_api import Response

from webtest.browser import Browser
from webtest.captcha import CaptchaClient, CaptchaError, detect_captcha, solve_captcha
from webtest.config import BrowserConfig
from webtest.forms import FormError, FormFillError, fill_form, submit

EXIT_OK, EXIT_FIELDS, EXIT_INVALID, EXIT_PAGE, EXIT_CAPTCHA, EXIT_CRASH = 0, 1, 2, 3, 4, 5

log = logging.getLogger(__name__)
captcha_log = logging.getLogger("webtest.captcha.step")
CAPTCHA_HOSTS = ("google.com/recaptcha", "recaptcha.net", "hcaptcha.com",
                 "challenges.cloudflare.com")

INVALID_JS = """
(root) => {
  const f = root.tagName === 'FORM' ? root : (root.closest('form') || root.querySelector('form'));
  if (!f) return {form: false, invalid: [], action: null, method: null};
  const invalid = f.noValidate ? [] : [...f.elements]
    .filter(e => e.willValidate && !e.checkValidity())
    .map(e => ({field: e.name || e.id || e.tagName.toLowerCase(), message: e.validationMessage}));
  return {form: true, invalid, action: f.action, method: (f.method || 'get').toUpperCase()};
}
"""

SENT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


# --------------------------------------------------------------------------- data
def load_data(raw: str) -> tuple[dict[str, Any], Path | None]:
    """DATA argument -> (ordered dict, directory of the JSON file or None)."""
    base: Path | None = None
    if raw == "-":
        text = sys.stdin.read()
    elif raw.lstrip().startswith(("{", "[")):
        text = raw
    else:
        path = Path(raw).expanduser()
        if not path.is_file():
            raise ValueError(f"DATA is neither JSON nor an existing file: {raw}")
        text = path.read_text(encoding="utf-8")
        base = path.resolve().parent
    data = json.loads(text)
    if isinstance(data, list) and all(isinstance(p, list) and len(p) == 2 for p in data):
        data = dict(data)
    if not isinstance(data, dict) or not data:
        raise ValueError('DATA must be a non-empty JSON object {"field": value}')
    return data, base


def resolve_relative_paths(data: dict[str, Any], base: Path | None) -> dict[str, Any]:
    """A relative path that does not exist in the cwd but does exist next to the JSON file
    is rewritten to that absolute path. Nothing else is touched."""
    if base is None:
        return data

    def fix(v: Any) -> Any:
        if isinstance(v, (str, Path)) and str(v) and not Path(v).is_absolute() \
                and not Path(v).exists():
            candidate = base / v
            if candidate.is_file():
                return candidate if isinstance(v, Path) else str(candidate)
        if isinstance(v, list):
            return [fix(x) for x in v]
        return v

    return {k: fix(v) for k, v in data.items()}


# --------------------------------------------------------------------------- run
@dataclass
class FormJob:
    """One form submission. Used by the CLI and by Python templates (form_job.py)."""

    url: str
    form: str
    data: dict[str, Any]
    base_dir: Path | None = None  # relative file paths are also looked up here
    button: str | None = None
    dry_run: bool = False
    human: bool = False
    type_delay: "float | tuple[float, float] | None" = None  # s per char; (min, max) = variable
    field_pause: "float | tuple[float, float] | None" = None  # s after each field
    headless: bool | None = None  # None = WEBTEST_HEADLESS from .env
    profile: str | None = None  # None = WEBTEST_PROFILE_DIR from .env
    timeout: int = 10_000
    settle_ms: int = 5_000
    out: str | None = None
    wait: bool = False
    captcha: str = "auto"  # "auto": free first, then API · "free": never pay · "off"
    captcha_action: str | None = None  # reCAPTCHA v3 action, when the site's is known
    confirm_regex: str | None = None  # looked up in the page text after submit -> report["confirmation_id"]
    result: dict[str, Any] = field(default_factory=dict)  # filled by run_job: the final report


def _config(job: FormJob) -> BrowserConfig:
    cfg = BrowserConfig.from_env()
    if job.headless is not None:
        cfg = cfg.with_(headless=job.headless)
    if job.profile:
        cfg = cfg.with_(profile_dir=Path(job.profile))
    return cfg


def _out_dir(job: FormJob) -> Path:
    if job.out:
        return Path(job.out)
    return Path("out") / f"webform-{datetime.now():%Y%m%d-%H%M%S}"


def submit_form(job: FormJob) -> int:
    """Blocking entry point: open, fill, validate, submit, report. Returns the exit code."""
    return asyncio.run(run_job(job))


async def run_job(job: FormJob) -> int:
    if not job.data:
        print("error: DATA is empty", file=sys.stderr)
        return EXIT_PAGE
    data = resolve_relative_paths(dict(job.data), job.base_dir)
    out = _out_dir(job)
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"url": job.url, "form": job.form, "fields": [], "requests": []}

    code = EXIT_CRASH
    try:
        async with Browser(_config(job)) as b:
            page = await b.page()
            try:
                code = await _run_in_page(b, page, job, data, report)
            except Exception as exc:  # noqa: BLE001  page closed, driver died, our bug...
                log.exception("form run crashed: %s", job.url)
                print(f"error: {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}",
                      file=sys.stderr)
                report["error"] = f"{type(exc).__name__}: {exc}"
            try:
                shot = await b.screenshot(page, out / "after.png")
                report["screenshot"] = str(shot)
            except Exception:  # noqa: BLE001
                pass
            if job.wait:
                try:
                    await asyncio.to_thread(input, "Browser is open. Press Enter to close... ")
                except (EOFError, OSError):
                    pass
    except Exception as exc:  # noqa: BLE001  browser could not start (profile locked, no Chrome)
        log.exception("browser failed: %s", job.url)
        print(f"error: browser: {type(exc).__name__}: {exc}", file=sys.stderr)
        report.setdefault("error", f"{type(exc).__name__}: {exc}")

    report["exit_code"] = code
    job.result.update(report, out=str(out))
    try:
        (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        print(f"\nreport: {out / 'report.json'}")
    except OSError as exc:
        log.error("cannot write report: %s", exc)
    return code


async def _run_in_page(b: Browser, page, job: FormJob, data, report) -> int:
    # 1. open
    try:
        status = await b.goto(page, job.url)
    except PWError as exc:
        print(f"error: cannot open {job.url}: {exc.message.splitlines()[0]}", file=sys.stderr)
        return EXIT_PAGE
    print(f"open   {status}  {page.url}")
    report["open_status"] = status
    if status is not None and status >= 400:
        print(f"error: page answered {status}", file=sys.stderr)
        return EXIT_PAGE

    # 2. the form
    forms = page.locator(job.form)
    try:
        await forms.first.wait_for(state="attached", timeout=job.timeout)
    except PWError:
        print(f"error: no element matches form selector {job.form!r}", file=sys.stderr)
        return EXIT_PAGE
    if (n := await forms.count()) > 1:
        print(f"error: form selector {job.form!r} matches {n} elements", file=sys.stderr)
        return EXIT_PAGE
    form = forms.first

    # 3. fill
    print("fill")
    try:
        results = await fill_form(
            form, data, human=job.human or job.type_delay is not None, timeout_ms=job.timeout,
            type_delay=job.type_delay, field_pause=job.field_pause)
        code = EXIT_OK
    except FormFillError as exc:
        results, code = exc.results, EXIT_FIELDS
    except (FormError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FIELDS
    for r in results:
        print(f"  {r}")
    report["fields"] = [vars(r) for r in results]
    if code != EXIT_OK:
        print("not submitted: some fields did not take their value", file=sys.stderr)
        return code

    # 4. record everything that leaves the page from here on: a captcha callback may
    #    submit the form by itself, and then our own click must not submit it twice
    seen: list[Response] = []

    def on_response(resp: Response) -> None:
        req = resp.request
        try:
            main_frame = req.frame == page.main_frame
        except PWError:  # request from a frame that is already gone
            return
        # captcha widgets talk to their servers from their own iframes; that is not our form
        if not main_frame or any(h in req.url for h in CAPTCHA_HOSTS):
            return
        sent = req.method in SENT_METHODS and req.resource_type in ("document", "xhr", "fetch")
        if sent or req.resource_type == "document":
            seen.append(resp)

    url_before = page.url
    page.on("response", on_response)

    # 5. captcha (right before submit: tokens expire in about 2 minutes)
    if job.captcha != "off":
        code = await _captcha_step(page, form, job, report)
        if code != EXIT_OK:
            return code

    # 6. the browser's own validation, then submit
    state = await form.evaluate(INVALID_JS)
    report["validation"] = state
    if seen:
        print("submit: the captcha callback submitted the form itself")
    elif state["invalid"]:
        print("not submitted: the browser would block this form")
        for item in state["invalid"]:
            print(f"  {item['field']}: {item['message']}")
        return EXIT_INVALID
    elif job.dry_run:
        print("dry run: filled, not submitted")
        return EXIT_OK
    else:
        try:
            await submit(form, job.button, timeout_ms=job.timeout)
        except FormError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_FIELDS
    try:
        await page.wait_for_load_state("load", timeout=job.timeout)
    except PWError:
        pass
    await b.settle(page, job.settle_ms)
    page.remove_listener("response", on_response)

    action = state.get("action")
    print("sent")
    for resp in seen:
        req = resp.request
        tag = "  (form action)" if action and req.url.split("#")[0] == action else ""
        print(f"  {req.method:<6} {resp.status}  {req.url}{tag}")
        report["requests"].append({"method": req.method, "status": resp.status, "url": req.url,
                                   "type": req.resource_type})
    if not seen:
        print("  no request seen: the page handled the submit in JavaScript or ignored it;"
              " check after.png")
    if job.confirm_regex:
        try:
            body = await page.inner_text("body", timeout=3_000)
        except PWError:
            body = ""
        m = re.search(job.confirm_regex, body)
        report["confirmation_id"] = m.group(0) if m else None
        print(f"confirmation id: {report['confirmation_id']}")
    title = await page.title()
    print(f"page   {url_before} -> {page.url}  {title!r}")
    report.update(url_before=url_before, url_after=page.url, title_after=title)
    failed = [r for r in report["requests"] if r["status"] >= 400]
    if failed:
        print(f"server answered an error: {failed[0]['status']} {failed[0]['url']}",
              file=sys.stderr)
    return EXIT_OK


async def _captcha_step(page, form, job: FormJob, report: dict[str, Any]) -> int:
    info = await detect_captcha(page, form)
    if info is None:
        return EXIT_OK
    flags = " invisible" if info.invisible else ""
    flags += " enterprise" if info.enterprise else ""
    print(f"captcha {info.kind} in {info.scope}, sitekey={info.sitekey}{flags}")
    report["captcha"] = {"kind": info.kind, "sitekey": info.sitekey, "scope": info.scope,
                         "invisible": info.invisible, "vendor": info.vendor}
    if job.dry_run:
        print("  dry run: not solved")
        return EXIT_OK
    client = CaptchaClient.from_env() if job.captcha == "auto" else None
    try:
        result = await solve_captcha(page, info, client, form=form, v3_action=job.captcha_action)
    except CaptchaError as exc:
        captcha_log.error("%s sitekey=%s on %s: %s", info.kind, info.sitekey, page.url, exc)
        print(f"not submitted: captcha {exc}", file=sys.stderr)
        report["captcha"]["error"] = str(exc)
        return EXIT_CAPTCHA
    except Exception as exc:  # noqa: BLE001  solver bug / network: log it, do not crash the run
        captcha_log.exception("%s solver crashed on %s", info.kind, page.url)
        print(f"not submitted: captcha solver crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
        report["captcha"]["error"] = f"{type(exc).__name__}: {exc}"
        return EXIT_CAPTCHA
    print(f"  {result}")
    captcha_log.info("%s solved: %s", info.kind, result)
    report["captcha"].update(vars(result))
    await page.wait_for_timeout(1_500)  # let the widget callback run
    return EXIT_OK


# --------------------------------------------------------------------------- CLI
def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("url", help="page with the form")
    p.add_argument("form", help="CSS selector of the form, e.g. 'form#contact'")
    p.add_argument("data", help='JSON object {"field": value}, a .json file, or "-" for stdin')
    p.add_argument("--button", help="submit control to click (default: the form's own)")
    p.add_argument("--dry-run", action="store_true", help="fill and validate, do not submit")
    p.add_argument("--human", action="store_true", help="type char by char with pauses")
    p.add_argument("--headless", action="store_true", help="override WEBTEST_HEADLESS")
    p.add_argument("--profile", help="override WEBTEST_PROFILE_DIR")
    p.add_argument("--timeout", type=int, default=10_000, help="ms to wait for each field")
    p.add_argument("--settle-ms", type=int, default=5_000, help="ms to wait after submit")
    p.add_argument("--out", help="folder for report.json and after.png (default out/webform-*)")
    p.add_argument("--wait", action="store_true", help="keep the window open until Enter")
    p.add_argument("--captcha", choices=["auto", "free", "off"], default="auto",
                   help="auto: pass for free if possible, else the API (captcha_key in .env); "
                        "free: never use the API; off: do not touch captchas")
    p.add_argument("--captcha-action", help="reCAPTCHA v3 action name, if known")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="webform",
        description="Open a URL, fill a form from JSON, submit it, show what was sent.",
        epilog="exit codes: 0 sent, 1 field problem, 2 browser validation, "
               "3 page/form missing, 4 captcha not solved, 5 crashed (see logs)",
    )
    add_arguments(p)
    return run_cli(p.parse_args(argv))


def run_cli(args: argparse.Namespace) -> int:
    try:
        data, base = load_data(args.data)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_PAGE
    return submit_form(FormJob(
        url=args.url, form=args.form, data=data, base_dir=base, button=args.button,
        dry_run=args.dry_run, human=args.human, headless=True if args.headless else None,
        profile=args.profile, timeout=args.timeout, settle_ms=args.settle_ms,
        out=args.out, wait=args.wait, captcha=args.captcha, captcha_action=args.captcha_action,
    ))


if __name__ == "__main__":
    raise SystemExit(main())
