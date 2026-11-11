"""Base browser control: patchright (undetected Playwright) driving real Google Chrome."""

from webtest.browser import Browser, ProfileInUse
from webtest.config import BrowserConfig
from webtest.forms import FieldResult, describe_form, fill_field, fill_form, submit

__all__ = [
    "Browser",
    "BrowserConfig",
    "FieldResult",
    "ProfileInUse",
    "describe_form",
    "fill_field",
    "fill_form",
    "submit",
]
