"""Captcha: detect in a form, pass for free when possible, otherwise solve via the API."""

from webtest.captcha.client import PROVIDERS, CaptchaClient, CaptchaError, CaptchaTimeout, Solution
from webtest.captcha.detect import CaptchaInfo, detect_captcha
from webtest.captcha.solve import (
    CaptchaResult,
    CaptchaUnavailable,
    solve_captcha,
    solve_form_captcha,
)

__all__ = [
    "PROVIDERS",
    "CaptchaClient",
    "CaptchaError",
    "CaptchaInfo",
    "CaptchaResult",
    "CaptchaTimeout",
    "CaptchaUnavailable",
    "Solution",
    "detect_captcha",
    "solve_captcha",
    "solve_form_captcha",
]
