from datetime import date, datetime, time
from pathlib import Path

import pytest

from webtest.forms import (
    Kind,
    accept_mismatch,
    classify,
    honeypot_suspect,
    is_file_value,
    looks_like_selector,
    normalize_files,
    to_bool,
    to_field_text,
)


@pytest.mark.parametrize(
    "key,expected",
    [
        ("#email", True), (".x", True), ("input[name=a]", True), ("form > input", True),
        ("css=input", True), ("xpath=//input", True), ("//input", True),
        ("First name", False), ("email", False), ("Dark mode", False),
    ],
)
def test_looks_like_selector(key, expected):
    assert looks_like_selector(key) is expected


@pytest.mark.parametrize(
    "value,kind,expected",
    [
        (date(1994, 5, 17), Kind.DATE, "1994-05-17"),
        (datetime(2026, 11, 12, 14, 0), Kind.DATETIME, "2026-11-12T14:00"),
        (datetime(2026, 11, 12, 14, 0), Kind.DATE, "2026-11-12"),
        (date(2026, 11, 12), Kind.DATETIME, "2026-11-12T00:00"),
        (time(7, 30), Kind.TIME, "07:30"),
        (time(7, 30, 5), Kind.TIME, "07:30:05"),
        (date(2026, 11, 1), Kind.MONTH, "2026-11"),
        (date(2026, 11, 11), Kind.WEEK, "2026-W46"),
        (date(2027, 1, 1), Kind.WEEK, "2026-W53"),  # ISO year differs from calendar year
        ("#FF8800", Kind.COLOR, "#ff8800"),
        (31, Kind.TEXT, "31"),
    ],
)
def test_to_field_text(value, kind, expected):
    assert to_field_text(value, kind) == expected


def test_bad_color_is_rejected():
    with pytest.raises(ValueError):
        to_field_text("orange", Kind.COLOR)


def test_to_bool():
    assert to_bool("yes") and to_bool(1) and to_bool(True)
    assert not to_bool("off") and not to_bool(0)
    with pytest.raises(ValueError):
        to_bool("maybe")


def _info(**kw):
    base = {"tag": "input", "type": "", "role": "", "list": None, "contentEditable": False,
            "visible": True, "offscreen": False, "ariaHidden": False}
    return base | kw


@pytest.mark.parametrize(
    "info,kind",
    [
        (_info(), Kind.TEXT),
        (_info(type="email"), Kind.TEXT),
        (_info(type="weird"), Kind.TEXT),
        (_info(tag="textarea"), Kind.TEXT),
        (_info(type="date"), Kind.DATE),
        (_info(type="datetime-local"), Kind.DATETIME),
        (_info(type="file"), Kind.FILE),
        (_info(type="hidden"), Kind.HIDDEN),
        (_info(type="submit"), Kind.BUTTON),
        (_info(role="combobox"), Kind.COMBOBOX),
        (_info(role="combobox", list="x"), Kind.TEXT),
        (_info(tag="select"), Kind.SELECT),
        (_info(tag="div", contentEditable=True), Kind.CONTENTEDITABLE),
        (_info(tag="div", role="switch"), Kind.ARIA_CHECKBOX),
        (_info(tag="div", role="combobox"), Kind.COMBOBOX),
        (_info(tag="button"), Kind.BUTTON),
        (_info(tag="div"), Kind.OTHER),
    ],
)
def test_classify(info, kind):
    assert classify(info) == kind


def test_honeypot_only_for_typeable_fields():
    assert honeypot_suspect(_info(offscreen=True), Kind.TEXT)
    assert honeypot_suspect(_info(visible=False), Kind.TEXT)
    assert not honeypot_suspect(_info(visible=False), Kind.FILE)
    assert not honeypot_suspect(_info(visible=False), Kind.CHECKBOX)
    assert not honeypot_suspect(_info(visible=False), Kind.SELECT)


def test_accept_mismatch():
    assert not accept_mismatch(None, "a.exe")
    assert not accept_mismatch("image/*", "a.png")
    assert accept_mismatch("image/*", "a.pdf")
    assert not accept_mismatch(".pdf,.txt", "a.TXT")
    assert not accept_mismatch("application/pdf", "a.pdf")
    assert accept_mismatch(".pdf", "a.docx")


def test_files(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert normalize_files(str(f)) == [f]
    assert normalize_files(None) == []
    payload = normalize_files({"name": "b.csv", "buffer": b"1,2"})[0]
    assert payload["mimeType"] == "text/csv"
    with pytest.raises(FileNotFoundError):
        normalize_files(tmp_path / "missing.txt")


def test_file_intent_needs_path_objects():
    assert is_file_value(Path("a.pdf"))
    assert is_file_value([Path("a"), {"name": "b", "buffer": b""}])
    assert not is_file_value("a.pdf")
    assert not is_file_value([])
