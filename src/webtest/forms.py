"""Fill every kind of form field, files included, and read each value back.

    from webtest.forms import fill_form, submit

    results = await fill_form(page, {
        "First name": "Olena",               # label / name / id / placeholder, auto
        "email": "olena@example.com",
        "Birthday": date(1994, 5, 17),       # date/time/datetime/month/week from Python objects
        "I agree": True,                     # checkbox / role=switch
        "size": "Medium",                    # radio group: value or label
        "topics": ["seo", "Development"],    # checkbox group: exactly these checked
        "country": "United States",          # <select>: value or label; list for multiple
        "Avatar": Path("me.png"),            # <input type=file>; list for multiple
        "#upload_btn": Path("cv.pdf"),       # a button that opens the file chooser
        "Fruit": "Banana",                   # custom role=combobox / autocomplete
        "Notes": "Hello",                    # contenteditable
    })
    await submit(page.locator("form#signup"))

Field keys
    * Locator object, used as is;
    * prefixed: ``label:``, ``name:``, ``id:``, ``placeholder:``;
    * anything that looks like a selector (``#x``, ``.x``, ``[x]``, ``a > b``, ``css=``,
      ``xpath=``, ``//...``) goes to ``root.locator()``;
    * a plain string is tried as name, id, label (incl. aria-label), placeholder, then CSS.
      The first strategy that matches wins, and it is reported in ``FieldResult.how``.

Every field is read back after filling (``FieldResult.ok``). The page is the truth, not
our intent: a masked phone input that reformats the value, a clamped range or a select
that rejected the option all show up as ``ok=False`` instead of silently passing.

Invisible text fields are refused (``HoneypotSuspected``). Anti-bot forms plant
off-screen inputs that a human never sees: filling one flags the whole submission.
Hidden file inputs and visually hidden checkboxes are normal UI and are handled
(files are set directly, checkboxes are clicked through their label).
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import random
import re
import time
from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as dtime
from enum import StrEnum
from pathlib import Path
from typing import Any

from patchright.async_api import Error as PWError
from patchright.async_api import Frame, Locator, Page
from patchright.async_api import TimeoutError as PWTimeout

log = logging.getLogger(__name__)

Root = Page | Frame | Locator


class Kind(StrEnum):
    TEXT = "text"  # text, email, password, number, tel, url, search, textarea, unknown types
    DATE = "date"
    TIME = "time"
    DATETIME = "datetime-local"
    MONTH = "month"
    WEEK = "week"
    COLOR = "color"
    RANGE = "range"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    SELECT = "select"
    FILE = "file"
    CONTENTEDITABLE = "contenteditable"
    COMBOBOX = "combobox"  # role=combobox: custom dropdowns and autocompletes
    ARIA_CHECKBOX = "aria-checkbox"  # role=checkbox / role=switch on a non-input
    HIDDEN = "hidden"  # <input type=hidden>
    BUTTON = "button"
    OTHER = "other"


TEXT_LIKE = {Kind.TEXT, Kind.DATE, Kind.TIME, Kind.DATETIME, Kind.MONTH, Kind.WEEK,
             Kind.COLOR, Kind.RANGE}
_VALUE_INPUT_TYPES = {"date": Kind.DATE, "time": Kind.TIME, "datetime-local": Kind.DATETIME,
                      "month": Kind.MONTH, "week": Kind.WEEK, "color": Kind.COLOR,
                      "range": Kind.RANGE}
_BUTTON_TYPES = {"submit", "button", "reset", "image"}


# ------------------------------------------------------------------- errors / results
class FormError(RuntimeError):
    pass


class FieldNotFound(FormError):
    pass


class AmbiguousField(FormError):
    pass


class FieldNotFillable(FormError):
    pass


class HoneypotSuspected(FieldNotFillable):
    pass


class OptionNotFound(FormError):
    pass


@dataclass
class FieldResult:
    key: str
    kind: str
    how: str
    expected: Any
    actual: Any
    ok: bool
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        mark = "ok " if self.ok else "BAD"
        extra = f"  ({'; '.join(self.notes)})" if self.notes else ""
        return f"[{mark}] {self.key} <{self.kind} via {self.how}> = {self.actual!r}{extra}"


class FormFillError(FormError):
    def __init__(self, failed: list[FieldResult], results: list[FieldResult]):
        self.failed = failed
        self.results = results
        lines = "\n".join(f"  {r}  expected {r.expected!r}" for r in failed)
        super().__init__(f"{len(failed)} field(s) did not take the value:\n{lines}")


# ------------------------------------------------------------------- element inspection
# Runs in patchright's isolated world: DOM properties are shared, page globals are not
# needed here, and the site's scripts cannot see this code.
INFO_FN = r"""
(el) => {
  const tag = el.tagName.toLowerCase();
  const cs = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  const left = r.left + window.scrollX, top = r.top + window.scrollY;
  let label = el.labels && el.labels[0] ? el.labels[0].innerText : '';
  if (!label) label = el.getAttribute('aria-label') || '';
  if (!label && el.getAttribute('aria-labelledby')) {
    label = el.getAttribute('aria-labelledby').split(/\s+/)
      .map(id => (document.getElementById(id) || {}).innerText || '').join(' ');
  }
  return {
    tag,
    type: (el.getAttribute('type') || '').toLowerCase(),
    role: (el.getAttribute('role') || '').toLowerCase(),
    name: el.getAttribute('name'),
    id: el.id || null,
    value: el.value ?? null,
    label: label.replace(/\s+/g, ' ').trim(),
    placeholder: el.getAttribute('placeholder'),
    required: !!el.required || el.getAttribute('aria-required') === 'true',
    multiple: !!el.multiple,
    accept: el.getAttribute('accept'),
    list: el.getAttribute('list'),
    contentEditable: !!el.isContentEditable,
    disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
    readOnly: !!el.readOnly,
    checked: typeof el.checked === 'boolean' ? el.checked : null,
    ariaChecked: el.getAttribute('aria-checked'),
    visible: r.width > 0 && r.height > 0 && cs.visibility !== 'hidden'
             && cs.display !== 'none' && parseFloat(cs.opacity) > 0,
    offscreen: left + r.width <= 0 || top + r.height <= 0,
    ariaHidden: !!el.closest('[aria-hidden="true"]'),
    options: tag === 'select'
      ? [...el.options].map(o => ({value: o.value, label: o.label.trim(), disabled: o.disabled}))
      : null,
  };
}
"""

FIELD_SELECTOR = (
    "input, select, textarea, [contenteditable=''], [contenteditable='true'], "
    "[role=combobox], [role=checkbox], [role=switch]"
)

SCAN_JS = (
    "(root) => { const info = " + INFO_FN + ";\n"
    "  return [...root.querySelectorAll(" + repr(FIELD_SELECTOR) + ")].map(info); }"
)


async def element_info(el: Locator) -> dict[str, Any]:
    return await el.evaluate(INFO_FN)


def classify(info: Mapping[str, Any]) -> Kind:
    tag, typ, role = info["tag"], info["type"], info["role"]
    if tag == "select":
        return Kind.SELECT
    if tag == "textarea":
        return Kind.TEXT
    if tag == "input":
        if typ == "hidden":
            return Kind.HIDDEN
        if typ == "file":
            return Kind.FILE
        if typ == "checkbox":
            return Kind.CHECKBOX
        if typ == "radio":
            return Kind.RADIO
        if typ in _VALUE_INPUT_TYPES:
            return _VALUE_INPUT_TYPES[typ]
        if typ in _BUTTON_TYPES:
            return Kind.BUTTON
        if role == "combobox" and not info.get("list"):
            return Kind.COMBOBOX
        return Kind.TEXT
    if info.get("contentEditable"):
        return Kind.CONTENTEDITABLE
    if role in ("checkbox", "switch"):
        return Kind.ARIA_CHECKBOX
    if role == "combobox":
        return Kind.COMBOBOX
    if tag == "button" or role == "button":
        return Kind.BUTTON
    return Kind.OTHER


def honeypot_suspect(info: Mapping[str, Any], kind: Kind) -> bool:
    """A typeable field a human cannot see. Checkboxes, radios, selects and file inputs
    are excluded: hiding the native control behind custom UI is ordinary design."""
    if kind not in TEXT_LIKE | {Kind.CONTENTEDITABLE, Kind.COMBOBOX}:
        return False
    return (not info["visible"]) or info["offscreen"] or info["ariaHidden"]


async def describe_form(root: Root) -> list[dict[str, Any]]:
    """Every field under `root` with its kind, label and a suggested fill key."""
    scope = root.locator("body") if isinstance(root, (Page, Frame)) else root
    raw = await scope.evaluate(SCAN_JS)
    out = []
    for info in raw:
        kind = classify(info)
        info["kind"] = str(kind)
        info["honeypot_suspect"] = honeypot_suspect(info, kind)
        info["key"] = (
            f"name:{info['name']}" if info["name"]
            else f"id:{info['id']}" if info["id"]
            else f"label:{info['label']}" if info["label"]
            else None
        )
        out.append(info)
    return out


# ------------------------------------------------------------------- field resolution
_ENGINE = re.compile(r"^(css|xpath|text|id|data-testid|role|internal:[\w-]+)=")


def looks_like_selector(key: str) -> bool:
    return (
        key.startswith(("#", ".", "[", "//", "(", "*"))
        or bool(_ENGINE.match(key))
        or any(c in key for c in "[]>~")
    )


def _css_str(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _strategies(root: Root, key: str | Locator) -> list[tuple[str, Locator]]:
    if isinstance(key, Locator):
        return [("locator", key)]
    prefixed = {
        "label:": lambda v: root.get_by_label(v, exact=True),
        "name:": lambda v: root.locator(f'[name="{_css_str(v)}"]'),
        "id:": lambda v: root.locator(f'[id="{_css_str(v)}"]'),
        "placeholder:": lambda v: root.get_by_placeholder(v, exact=True),
    }
    for prefix, make in prefixed.items():
        if key.startswith(prefix):
            return [(prefix[:-1], make(key[len(prefix):]))]
    if looks_like_selector(key):
        return [("selector", root.locator(key))]
    return [
        ("name", prefixed["name:"](key)),
        ("id", prefixed["id:"](key)),
        ("label", prefixed["label:"](key)),
        ("placeholder", prefixed["placeholder:"](key)),
        ("selector", root.locator(key)),
    ]


async def resolve(root: Root, key: str | Locator, timeout_ms: int = 10_000) -> tuple[str, Locator]:
    """First strategy with at least one match; polls until `timeout_ms` so fields that
    appear late (dependent fields, wizard steps) are found."""
    strategies = _strategies(root, key)
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        for how, loc in strategies:
            try:
                if await loc.count():
                    return how, loc
            except PWError:  # e.g. a plain label is not valid CSS
                continue
        if time.monotonic() >= deadline:
            tried = ", ".join(h for h, _ in strategies)
            raise FieldNotFound(f"{key!r}: nothing matched ({tried}) within {timeout_ms} ms")
        await asyncio.sleep(0.2)


def _page_of(root: Root | Locator) -> Page:
    return root if isinstance(root, Page) else root.page


# ------------------------------------------------------------------- value normalization
_TRUE = {"1", "true", "yes", "on", "y", "checked"}
_FALSE = {"0", "false", "no", "off", "n", "unchecked", ""}


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
        return value.strip().lower() in _TRUE
    raise ValueError(f"cannot read {value!r} as checked/unchecked")


def _hm(t: dtime) -> str:
    return f"{t.hour:02d}:{t.minute:02d}" + (f":{t.second:02d}" if t.second else "")


def _week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def to_field_text(value: Any, kind: Kind) -> str:
    """Python value -> the exact string the browser's input expects."""
    if isinstance(value, datetime):
        return {
            Kind.DATE: lambda: value.date().isoformat(),
            Kind.TIME: lambda: _hm(value.time()),
            Kind.MONTH: lambda: value.strftime("%Y-%m"),
            Kind.WEEK: lambda: _week(value.date()),
        }.get(kind, lambda: f"{value.date().isoformat()}T{_hm(value.time())}")()
    if isinstance(value, date):
        if kind == Kind.MONTH:
            return value.strftime("%Y-%m")
        if kind == Kind.WEEK:
            return _week(value)
        if kind == Kind.DATETIME:
            return f"{value.isoformat()}T00:00"
        return value.isoformat()
    if isinstance(value, dtime):
        return _hm(value)
    text = str(value)
    if kind == Kind.COLOR:
        text = text.strip().lower()
        if not re.fullmatch(r"#[0-9a-f]{6}", text):
            raise ValueError(f"color must be #rrggbb, got {value!r}")
    return text


# ------------------------------------------------------------------- files
FileValue = Path | Mapping[str, Any]


def is_file_value(value: Any) -> bool:
    """Path objects and payload dicts are unambiguous file intent; plain strings are not
    (a string is a path only when the target is an <input type=file>)."""
    if isinstance(value, Path):
        return True
    if isinstance(value, Mapping) and "buffer" in value:
        return True
    if isinstance(value, (list, tuple)) and value:
        return all(is_file_value(v) for v in value)
    return False


def normalize_files(value: Any) -> list[Any]:
    if value is None or value == [] or value == ():
        return []
    items: Iterable[Any] = value if isinstance(value, (list, tuple)) else [value]
    out: list[Any] = []
    for item in items:
        if isinstance(item, Mapping):
            if "name" not in item or "buffer" not in item:
                raise ValueError("file payload needs 'name' and 'buffer'")
            mime = item.get("mimeType") or mimetypes.guess_type(item["name"])[0]
            out.append({"name": item["name"], "mimeType": mime or "application/octet-stream",
                        "buffer": item["buffer"]})
        else:
            path = Path(item).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"file to upload does not exist: {path}")
            out.append(path)
    return out


def _file_name(f: Any) -> str:
    return f["name"] if isinstance(f, Mapping) else Path(f).name


def accept_mismatch(accept: str | None, name: str, mime: str | None = None) -> bool:
    """True when `accept` is set and the file matches none of its tokens."""
    if not accept:
        return False
    tokens = [t.strip().lower() for t in accept.split(",") if t.strip()]
    ext = Path(name).suffix.lower()
    mime = (mime or mimetypes.guess_type(name)[0] or "").lower()
    for t in tokens:
        if t.startswith(".") and ext == t:
            return False
        if t.endswith("/*") and mime.startswith(t[:-1]):
            return False
        if t == mime:
            return False
    return True


async def upload_via_chooser(trigger: Locator, files: Any, timeout_ms: int = 10_000) -> list[str]:
    """Click something that opens the OS file dialog and answer it with `files`.
    For upload widgets whose <input type=file> is created on click or lives elsewhere."""
    items = normalize_files(files)
    page = trigger.page
    async with page.expect_file_chooser(timeout=timeout_ms) as chooser_info:
        await trigger.click()
    chooser = await chooser_info.value
    if len(items) > 1 and not chooser.is_multiple():
        raise FieldNotFillable(f"{len(items)} files given, the chooser accepts one")
    await chooser.set_files(items)
    return await chooser.element.evaluate("el => [...el.files].map(f => f.name)")


# ------------------------------------------------------------------- typing
_DEFAULT_TYPE_DELAY = (0.04, 0.14)
_DEFAULT_FIELD_PAUSE = (0.25, 0.7)
_pace: ContextVar[tuple[Any, Any]] = ContextVar("pace", default=(None, None))


def _secs(v: Any, default: tuple[float, float]) -> float:
    v = default if v is None else v
    if isinstance(v, (int, float)):
        return float(v)
    return random.uniform(*v)


async def _type_text(el: Locator, text: str, human: bool) -> None:
    if not human:
        await el.fill(text)
        return
    await el.click()
    await el.fill("")
    for ch in text:
        await el.press_sequentially(ch)
        await asyncio.sleep(_secs(_pace.get()[0], _DEFAULT_TYPE_DELAY))


async def _set_checked(el: Locator, info: Mapping[str, Any], want: bool, root: Root) -> None:
    """Visible control: normal check. Visually hidden control (custom styled): click its
    label with a real pointer event, the way a person does."""
    if info["visible"]:
        await el.set_checked(want)
        return
    if await el.is_checked() == want:
        return
    label = None
    if info["id"]:
        candidate = root.locator(f'label[for="{_css_str(info["id"])}"]')
        if await candidate.count():
            label = candidate.first
    if label is None:
        candidate = el.locator("xpath=ancestor::label[1]")
        if await candidate.count():
            label = candidate.first
    if label is not None:
        await label.click()
    else:
        await el.set_checked(want, force=True)


def _option_matches(info: Mapping[str, Any], want: Any) -> bool:
    text = str(want)
    return info.get("value") == text or (info.get("label") or "").casefold() == text.casefold()


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


# ------------------------------------------------------------------- per-kind fillers
async def _fill_text_like(el, info, kind, value, human) -> tuple[Any, bool, list[str]]:
    if info["readOnly"]:
        raise FieldNotFillable("field is read-only")
    text = to_field_text(value, kind)
    if kind in (Kind.TEXT,):
        await _type_text(el, text, human)
    else:  # date/time/color/range: typing is locale dependent, set the value instead
        await el.fill(text)
    actual = await el.input_value()
    if actual == text:
        return actual, True, []
    if text and _digits(actual) == _digits(text) and _digits(text):
        return actual, True, ["reformatted by the page (input mask)"]
    return actual, False, []


async def _fill_select(el, info, value) -> tuple[Any, bool, list[str]]:
    wanted = list(value) if isinstance(value, (list, tuple)) else [value]
    if len(wanted) > 1 and not info["multiple"]:
        raise FieldNotFillable(f"{len(wanted)} options given, the select is single choice")
    chosen: list[str] = []
    for want in wanted:
        match = next((o for o in info["options"] if _option_matches(o, want)), None)
        if match is None:
            labels = [o["label"] for o in info["options"]][:30]
            raise OptionNotFound(f"no option {want!r}; available: {labels}")
        if match["disabled"]:
            raise OptionNotFound(f"option {want!r} is disabled")
        chosen.append(match["value"])
    # custom dropdown libraries hide the native select and listen to its change event
    await el.select_option(value=chosen, force=not info["visible"])
    actual = await el.evaluate("el => [...el.selectedOptions].map(o => o.value)")
    notes = [] if info["visible"] else ["native select is hidden (custom dropdown UI)"]
    return actual, sorted(actual) == sorted(chosen), notes


async def _fill_file(el, info, value) -> tuple[Any, bool, list[str]]:
    items = normalize_files(value)
    if len(items) > 1 and not info["multiple"]:
        raise FieldNotFillable(f"{len(items)} files given, the input accepts one")
    notes = [
        f"{_file_name(f)} does not match accept={info['accept']!r}"
        for f in items
        if accept_mismatch(info["accept"], _file_name(f),
                           f.get("mimeType") if isinstance(f, Mapping) else None)
    ]
    await el.set_input_files(items)
    actual = await el.evaluate("el => [...el.files].map(f => f.name)")
    return actual, actual == [_file_name(f) for f in items], notes


async def _fill_contenteditable(el, value, human) -> tuple[Any, bool, list[str]]:
    text = str(value)
    page = el.page
    await el.click()
    await page.keyboard.press("ControlOrMeta+A")
    await page.keyboard.press("Delete")
    if human:
        await page.keyboard.type(
            text, delay=_secs(_pace.get()[0], (0.04, 0.12)) * 1000)
    else:
        await page.keyboard.insert_text(text)
    actual = (await el.inner_text()).strip()
    return actual, actual == text.strip(), []


async def _fill_combobox(el, info, value, human) -> tuple[Any, bool, list[str]]:
    text = str(value)
    page = el.page
    is_input = info["tag"] == "input"
    if is_input:
        await _type_text(el, text, human)
    else:
        await el.click()
    option = page.get_by_role("option", name=text, exact=True)
    picked = True
    try:
        await option.first.wait_for(state="visible", timeout=3_000)
        await option.first.click()
    except PWTimeout:
        picked = False
    actual = await el.input_value() if is_input else (await el.inner_text()).strip()
    notes = [] if picked else ["no matching option appeared"]
    if is_input:
        ok = actual.casefold() == text.casefold() or (picked and text.casefold() in actual.casefold())
    else:
        ok = picked and text.casefold() in actual.casefold()
    return actual, ok, notes


async def _fill_aria_checkbox(el, value) -> tuple[Any, bool, list[str]]:
    want = to_bool(value)
    if (await el.get_attribute("aria-checked") == "true") != want:
        await el.click()
    actual = await el.get_attribute("aria-checked") == "true"
    return actual, actual == want, []


async def _fill_group(root, key, how, loc, infos, kind, value) -> FieldResult:
    """Radio group (one value) or checkbox group (exactly these values checked)."""
    n = len(infos)
    if kind == Kind.RADIO:
        idx = next((i for i, inf in enumerate(infos) if _option_matches(inf, value)), None)
        if idx is None:
            raise OptionNotFound(f"{key!r}: no radio {value!r}; have "
                                 f"{[i['label'] or i['value'] for i in infos]}")
        await _set_checked(loc.nth(idx), infos[idx], True, root)
        actual = infos[idx]["value"] if await loc.nth(idx).is_checked() else None
        return FieldResult(str(key), "radio", how, value, actual, actual is not None)

    wanted = list(value) if isinstance(value, (list, tuple, set)) else [value]
    targets: set[int] = set()
    for want in wanted:
        idx = next((i for i, inf in enumerate(infos) if _option_matches(inf, want)), None)
        if idx is None:
            raise OptionNotFound(f"{key!r}: no checkbox {want!r}; have "
                                 f"{[i['label'] or i['value'] for i in infos]}")
        targets.add(idx)
    for i in range(n):
        await _set_checked(loc.nth(i), infos[i], i in targets, root)
    actual = [infos[i]["value"] for i in range(n) if await loc.nth(i).is_checked()]
    expected = [infos[i]["value"] for i in sorted(targets)]
    return FieldResult(str(key), "checkbox-group", how, wanted, actual, actual == expected)


# ------------------------------------------------------------------- public API
async def fill_field(
    root: Root,
    key: str | Locator,
    value: Any,
    *,
    human: bool = False,
    allow_hidden: bool = False,
    timeout_ms: int = 10_000,
) -> FieldResult:
    """Fill one field and read it back. `value=None` leaves the field untouched."""
    how, loc = await resolve(root, key, timeout_ms)
    count = await loc.count()

    if count > 1:
        infos = [await element_info(loc.nth(i)) for i in range(count)]
        kinds = {classify(i) for i in infos}
        if kinds in ({Kind.RADIO}, {Kind.CHECKBOX}):
            if value is None:
                return FieldResult(str(key), kinds.pop(), how, None, None, True, ["skipped"])
            return await _fill_group(root, key, how, loc, infos, kinds.pop(), value)
        raise AmbiguousField(
            f"{key!r} matches {count} elements via {how} ({sorted(map(str, kinds))}); "
            "use a narrower key or scope `root` to the form"
        )

    el = loc.first
    info = await element_info(el)
    kind = classify(info)
    if value is None:
        return FieldResult(str(key), str(kind), how, None, None, True, ["skipped"])
    if info["disabled"]:
        raise FieldNotFillable(f"{key!r} is disabled")
    if honeypot_suspect(info, kind) and not allow_hidden:
        raise HoneypotSuspected(
            f"{key!r} is a {kind} field a person cannot see (hidden/off-screen/aria-hidden). "
            "Anti-bot forms use such fields as traps. If it only appears after another step, "
            "do that step first; pass allow_hidden=True only if you are sure."
        )

    notes: list[str] = []
    if kind in TEXT_LIKE:
        actual, ok, notes = await _fill_text_like(el, info, kind, value, human)
    elif kind == Kind.CHECKBOX:
        want = to_bool(value)
        await _set_checked(el, info, want, _scope_for_labels(root, el))
        actual = await el.is_checked()
        ok = actual == want
    elif kind == Kind.RADIO:
        if not to_bool(value):
            raise FieldNotFillable(f"{key!r}: a radio cannot be unchecked; choose another one")
        await _set_checked(el, info, True, _scope_for_labels(root, el))
        actual = await el.is_checked()
        ok = actual
    elif kind == Kind.SELECT:
        actual, ok, notes = await _fill_select(el, info, value)
    elif kind == Kind.FILE:
        actual, ok, notes = await _fill_file(el, info, value)
    elif kind == Kind.CONTENTEDITABLE:
        actual, ok, notes = await _fill_contenteditable(el, value, human)
    elif kind == Kind.COMBOBOX:
        actual, ok, notes = await _fill_combobox(el, info, value, human)
    elif kind == Kind.ARIA_CHECKBOX:
        actual, ok, notes = await _fill_aria_checkbox(el, value)
    elif kind == Kind.HIDDEN:
        if not allow_hidden:
            raise FieldNotFillable(
                f"{key!r} is <input type=hidden>: the page sets it, a person cannot. "
                "Pass allow_hidden=True to overwrite it anyway."
            )
        text = str(value)
        await el.evaluate("(el, v) => { el.value = v; }", text)
        actual = await el.input_value()
        ok, notes = actual == text, ["hidden input overwritten by script"]
    elif is_file_value(value):  # a button/area that opens the file dialog
        actual = await upload_via_chooser(el, value, timeout_ms)
        kind_label = "file-chooser"
        expected_names = [_file_name(f) for f in normalize_files(value)]
        return FieldResult(str(key), kind_label, how, value, actual, actual == expected_names)
    else:
        raise FieldNotFillable(f"{key!r} is a {kind} ({info['tag']}), not a form field")

    return FieldResult(str(key), str(kind), how, value, actual, ok, notes)


def _scope_for_labels(root: Root, el: Locator) -> Root:
    # label[for=id] may sit outside a Locator root: search the whole page/frame for it
    return root if isinstance(root, (Page, Frame)) else el.page


async def fill_form(
    root: Root,
    data: Mapping[Any, Any] | Iterable[tuple[Any, Any]],
    *,
    human: bool = False,
    allow_hidden: bool = False,
    timeout_ms: int = 10_000,
    strict: bool = True,
    type_delay: "float | tuple[float, float] | None" = None,
    field_pause: "float | tuple[float, float] | None" = None,
) -> list[FieldResult]:
    """Fill fields in the given order (order matters: dependent fields come after the
    field that reveals them). Missing/ambiguous/unfillable fields raise immediately;
    fields that did not keep their value raise FormFillError at the end when `strict`.

    `type_delay`: seconds between typed characters, fixed or a (min, max) range (human typing).
    `field_pause`: seconds to wait after each field, fixed or a (min, max) range."""
    _pace.set((type_delay, field_pause))
    items = data.items() if isinstance(data, Mapping) else data
    results: list[FieldResult] = []
    for key, value in items:
        result = await fill_field(root, key, value, human=human, allow_hidden=allow_hidden,
                                  timeout_ms=timeout_ms)
        log.info("%s", result)
        results.append(result)
        if human or field_pause is not None:
            await asyncio.sleep(_secs(field_pause, _DEFAULT_FIELD_PAUSE))
    failed = [r for r in results if not r.ok]
    if strict and failed:
        raise FormFillError(failed, results)
    return results


SUBMIT_SELECTOR = "button[type=submit], input[type=submit], input[type=image], button:not([type])"


async def submit(root: Root, button: str | Locator | None = None, timeout_ms: int = 10_000) -> None:
    """Click the submit control. With a Page/Frame root and no `button`, the page must have
    exactly one visible submit control, otherwise a header search button could be hit."""
    if button is not None:
        _, loc = await resolve(root, button, timeout_ms)
        await loc.first.click()
        return
    candidates = root.locator(SUBMIT_SELECTOR).filter(visible=True)
    n = await candidates.count()
    if n == 0:
        raise FieldNotFound("no visible submit control; pass button=")
    if n > 1 and isinstance(root, (Page, Frame)):
        raise AmbiguousField(f"{n} submit controls on the page; pass button= or a form root")
    await candidates.first.click()
