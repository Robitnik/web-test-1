"""webtest CLI: doctor / open / check."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from webtest.browser import Browser
from webtest.chrome import chrome_version, find_chrome
from webtest.config import BrowserConfig
from webtest.diagnostics import fingerprint, red_flags
from webtest.profile import lock_owner

CHECK_SITES = {
    "sannysoft": "https://bot.sannysoft.com/",
    "creepjs": "https://abrahamjuliot.github.io/creepjs/",
    "browserscan": "https://www.browserscan.net/bot-detection",
}


def _config(args: argparse.Namespace) -> BrowserConfig:
    cfg = BrowserConfig.from_env()
    if getattr(args, "headless", False):
        cfg = cfg.with_(headless=True)
    if getattr(args, "profile", None):
        cfg = cfg.with_(profile_dir=Path(args.profile))
    return cfg


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = _config(args)
    chrome = find_chrome(cfg.chrome_path)
    print(f"python      {sys.version.split()[0]}")
    print(f"patchright  {version('patchright')}")
    if chrome:
        print(f"chrome      {chrome}  ({chrome_version(chrome) or 'version unknown'})")
    else:
        print("chrome      NOT FOUND -> bundled Chromium fallback; run scripts/install_chrome.sh")
    print(f"profile     {cfg.profile_dir.resolve()}  lock={lock_owner(cfg.profile_dir)}")
    print(f"headless    {cfg.headless}")
    print(f"proxy       {'set' if cfg.proxy else 'none'}")
    return 0 if chrome else 1


async def _open(args: argparse.Namespace) -> int:
    async with Browser(_config(args)) as b:
        page = await b.page()
        status = await b.goto(page, args.url)
        print(f"{status}  {page.url}")
        print(f"title: {await page.title()}")
        if args.shot:
            print(f"screenshot: {await b.screenshot(page, args.shot)}")
        if args.wait:
            await asyncio.to_thread(input, "Browser is open. Press Enter to close... ")
    return 0


async def _check(args: argparse.Namespace) -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path("out") / f"check-{stamp}"
    async with Browser(_config(args)) as b:
        page = await b.page()
        await b.goto(page, "https://example.com/")
        fp = await fingerprint(page)
        flags = red_flags(fp)
        out.mkdir(parents=True, exist_ok=True)
        (out / "fingerprint.json").write_text(json.dumps(fp, indent=2, ensure_ascii=False))
        print(json.dumps(fp, indent=2, ensure_ascii=False))
        for name in args.sites:
            await b.goto(page, CHECK_SITES[name], settle_ms=10_000)
            print(f"{name}: {await b.screenshot(page, out / f'{name}.png')}")
    print(f"\nred flags: {len(flags)}")
    for f in flags:
        print(f"  - {f}")
    print(f"saved to {out}")
    return 1 if flags else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="webtest")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--headless", action="store_true", help="override WEBTEST_HEADLESS")
        sp.add_argument("--profile", help="override WEBTEST_PROFILE_DIR")

    d = sub.add_parser("doctor", help="show Chrome, patchright and profile state")
    common(d)

    o = sub.add_parser("open", help="open a URL in the controlled Chrome")
    common(o)
    o.add_argument("url")
    o.add_argument("--shot", help="save a full-page screenshot here")
    o.add_argument("--wait", action="store_true", help="keep the window open until Enter")

    c = sub.add_parser("check", help="fingerprint self-check (+ optional public test pages)")
    common(c)
    c.add_argument(
        "--sites", nargs="*", default=[], choices=sorted(CHECK_SITES),
        help="also screenshot these public bot-detection test pages",
    )

    args = p.parse_args(argv)
    if args.cmd == "doctor":
        return cmd_doctor(args)
    if args.cmd == "open":
        return asyncio.run(_open(args))
    return asyncio.run(_check(args))


if __name__ == "__main__":
    raise SystemExit(main())
