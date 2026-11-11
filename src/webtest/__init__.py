"""Base browser control: patchright (undetected Playwright) driving real Google Chrome."""

from webtest.browser import Browser, ProfileInUse
from webtest.config import BrowserConfig

__all__ = ["Browser", "BrowserConfig", "ProfileInUse"]
