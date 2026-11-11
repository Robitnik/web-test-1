"""Fills tests/fixtures/all_fields.html in a real browser.
Run: WEBTEST_LIVE=1 uv run pytest -m live"""

import asyncio
import os
from datetime import date, datetime, time
from pathlib import Path

import pytest

from webtest import Browser, BrowserConfig
from webtest.forms import (
    AmbiguousField,
    FieldNotFillable,
    FieldNotFound,
    HoneypotSuspected,
    describe_form,
    fill_field,
    fill_form,
    submit,
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("WEBTEST_LIVE") != "1", reason="set WEBTEST_LIVE=1"),
]

FIXTURE = (Path(__file__).parent / "fixtures" / "all_fields.html").resolve().as_uri()


def run(tmp_path, scenario):
    cfg = BrowserConfig(
        profile_dir=tmp_path / "profile",
        downloads_dir=tmp_path / "dl",
        headless=os.environ.get("WEBTEST_HEADLESS", "1") == "1",
    )

    async def main():
        async with Browser(cfg) as b:
            page = await b.page()
            await b.goto(page, FIXTURE, settle_ms=0)
            return await scenario(b, page)

    return asyncio.run(main())


@pytest.fixture
def files(tmp_path):
    out = {}
    for name, data in {"me.png": b"\x89PNG\r\n", "a.pdf": b"%PDF-1.4", "b.txt": b"hi",
                       "cv.pdf": b"%PDF-1.4"}.items():
        (tmp_path / name).write_bytes(data)
        out[name] = tmp_path / name
    return out


def test_fill_every_kind_and_submit(tmp_path, files):
    data = {
        "First name": "Olena",
        "email": "olena@example.com",
        "placeholder:Password": "s3cret!",
        "age": 31,
        "Phone": "+380501234567",
        "site": "https://example.com",
        "q": "patchright",
        "Bio": "Line one\nLine two",
        "Birthday": date(1994, 5, 17),
        "alarm": time(7, 30),
        "meeting": datetime(2026, 11, 12, 14, 0),
        "month": date(2026, 11, 1),
        "week": date(2026, 11, 11),
        "color": "#FF8800",
        "volume": 70,
        "I agree": True,
        "news": True,  # visually hidden checkbox, clicked through its label
        "size": "Medium",
        "topics": ["seo", "Development"],  # "ads" was pre-checked and must be unchecked
        "country": "United States",
        "langs": ["en", "Ukrainian"],
        "Avatar": files["me.png"],
        "docs": [files["a.pdf"], files["b.txt"]],
        "#upload_btn": files["cv.pdf"],  # button -> file chooser -> hidden input
        "City": "Lviv",
        "Notes": "Hello from webtest",
        "Dark mode": True,
        "Fruit": "Banana",
    }

    async def scenario(b, page):
        form = page.locator("#f")
        results = await fill_form(form, data)
        await submit(form)
        return results, await b.eval_main(page, "window.__submitted")

    results, sent = run(tmp_path, scenario)
    assert all(r.ok for r in results), [str(r) for r in results if not r.ok]
    kinds = {r.key: r.kind for r in results}
    assert kinds["#upload_btn"] == "file-chooser"
    assert kinds["topics"] == "checkbox-group"
    assert kinds["Fruit"] == "combobox"

    assert sent["first_name"] == ["Olena"]
    assert sent["password"] == ["s3cret!"]
    assert sent["bio"] == ["Line one\nLine two"]
    assert sent["birthday"] == ["1994-05-17"]
    assert sent["alarm"] == ["07:30"]
    assert sent["meeting"] == ["2026-11-12T14:00"]
    assert sent["month"] == ["2026-11"]
    assert sent["week"] == ["2026-W46"]
    assert sent["color"] == ["#ff8800"]
    assert sent["volume"] == ["70"]
    assert sent["agree"] == ["yes"]
    assert sent["news"] == ["yes"]
    assert sent["size"] == ["m"]
    assert sent["topics"] == ["seo", "dev"]
    assert sent["country"] == ["us"]
    assert sent["langs"] == ["en", "uk"]
    assert sent["avatar"] == ["me.png"]
    assert sent["docs"] == ["a.pdf", "b.txt"]
    assert sent["resume"] == ["cv.pdf"]
    assert sent["city"] == ["Lviv"]
    assert sent["notes"] == "Hello from webtest"
    assert sent["dark"] == "true"
    assert sent["fruit"] == "banana"
    assert sent["website_hp"] == [""]  # the trap stayed empty
    assert sent["token"] == ["abc"]
    assert "locked" not in sent


def test_human_typing(tmp_path):
    async def scenario(b, page):
        return await fill_form(page, {"First name": "Ann", "Notes": "hi"}, human=True)

    results = run(tmp_path, scenario)
    assert [r.actual for r in results] == ["Ann", "hi"]


def test_refusals(tmp_path):
    async def scenario(b, page):
        errors = {}
        for key, value, kw in [
            ("website_hp", "spam", {}),
            ("token", "x", {}),
            ("Locked", "y", {}),
            ("No such field", "z", {"timeout_ms": 300}),
            ("input[type=email], input[type=url]", "a", {}),
        ]:
            try:
                await fill_field(page, key, value, **kw)
                errors[key] = None
            except Exception as exc:  # noqa: BLE001
                errors[key] = type(exc)
        return errors

    errors = run(tmp_path, scenario)
    assert errors["website_hp"] is HoneypotSuspected
    assert errors["token"] is FieldNotFillable
    assert errors["Locked"] is FieldNotFillable
    assert errors["No such field"] is FieldNotFound
    assert errors["input[type=email], input[type=url]"] is AmbiguousField


def test_describe_form(tmp_path):
    fields = run(tmp_path, lambda b, page: describe_form(page.locator("#f")))
    by_name = {f["name"]: f for f in fields if f["name"]}
    assert by_name["website_hp"]["honeypot_suspect"] is True
    assert by_name["first_name"]["honeypot_suspect"] is False
    assert by_name["avatar"]["kind"] == "file"
    assert by_name["resume"]["kind"] == "file"
    assert [o["label"] for o in by_name["country"]["options"]][1:3] == ["Ukraine", "United States"]
    assert any(f["kind"] == "combobox" for f in fields)
    assert any(f["kind"] == "contenteditable" for f in fields)
