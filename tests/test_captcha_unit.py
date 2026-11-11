import asyncio

import pytest

from webtest.captcha import CaptchaClient, CaptchaError, Solution
from webtest.captcha.detect import (
    HCAPTCHA,
    RECAPTCHA_V2,
    TURNSTILE,
    kind_from_sitekey,
    sitekey_from_iframe_src,
)

KEY = "0123456789abcdef0123456789abcdef"


class FakeTransport:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def __call__(self, url, body):
        self.calls.append((url, body))
        return self.replies.pop(0)


def client(replies, **kw):
    t = FakeTransport(replies)
    return CaptchaClient(KEY, "2captcha", poll_s=0, transport=t, **kw), t


def test_solve_polls_until_ready():
    c, t = client([
        {"errorId": 0, "taskId": 77},
        {"errorId": 0, "status": "processing"},
        {"errorId": 0, "status": "ready", "solution": {"token": "TOK"}, "cost": "0.00145"},
    ])
    sol = asyncio.run(c.solve({"type": "TurnstileTaskProxyless"}, first_poll_s=0))
    assert sol.token == "TOK" and sol.task_id == 77 and sol.cost == "0.00145"
    assert [u.rsplit("/", 1)[1] for u, _ in t.calls] == ["createTask", "getTaskResult",
                                                         "getTaskResult"]
    assert t.calls[0][0].startswith("https://api.2captcha.com/")
    assert t.calls[0][1]["clientKey"] == KEY


def test_no_slot_is_retried():
    c, _ = client([
        {"errorId": 1, "errorCode": "ERROR_NO_SLOT_AVAILABLE"},
        {"errorId": 0, "taskId": 1},
    ])
    assert asyncio.run(c.create_task({"type": "x"})) == 1


def test_api_error_carries_code_not_key():
    c, _ = client([{"errorId": 1, "errorCode": "ERROR_ZERO_BALANCE",
                    "errorDescription": "no money"}])
    with pytest.raises(CaptchaError) as e:
        asyncio.run(c.balance())
    assert e.value.code == "ERROR_ZERO_BALANCE"
    assert KEY not in str(e.value) and KEY not in repr(c)


def test_timeout():
    c, _ = client([{"errorId": 0, "taskId": 5}] + [{"errorId": 0, "status": "processing"}] * 50,
                  timeout_s=0)
    with pytest.raises(CaptchaError):
        asyncio.run(c.solve({"type": "x"}, first_poll_s=0))


def test_from_env_reads_lowercase_key_and_provider():
    assert CaptchaClient.from_env({}) is None
    c = CaptchaClient.from_env({"captcha_key": KEY, "CAPTCHA_PROVIDER": "anti-captcha"})
    assert c.provider == "anticaptcha" and c.base_url == "https://api.anti-captcha.com"
    with pytest.raises(ValueError):
        CaptchaClient(KEY, "nope")


def test_solution_token_fields():
    assert Solution(1, {"gRecaptchaResponse": "g"}, 0).token == "g"
    assert Solution(1, {"text": "abc12"}, 0).token == "abc12"
    with pytest.raises(CaptchaError):
        Solution(1, {}, 0).token  # noqa: B018


@pytest.mark.parametrize("key,kind", [
    ("6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI", RECAPTCHA_V2),
    ("10000000-ffff-ffff-ffff-000000000001", HCAPTCHA),
    ("0x4AAAAAAABkMYinukE8nzY", TURNSTILE),
    ("1x00000000000000000000AA", TURNSTILE),
    ("whatever", None),
])
def test_kind_from_sitekey(key, kind):
    assert kind_from_sitekey(key) == kind


def test_sitekey_from_iframes():
    assert sitekey_from_iframe_src(
        "https://www.google.com/recaptcha/api2/anchor?ar=1&k=6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"
        "&co=aHR0&size=invisible"
    ) == (RECAPTCHA_V2, "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI", False)
    assert sitekey_from_iframe_src(
        "https://www.google.com/recaptcha/enterprise/anchor?k=6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"
    )[2] is True
    assert sitekey_from_iframe_src(
        "https://newassets.hcaptcha.com/captcha/v1/x/static/hcaptcha.html#frame=checkbox"
        "&sitekey=10000000-ffff-ffff-ffff-000000000001"
    ) == (HCAPTCHA, "10000000-ffff-ffff-ffff-000000000001", False)
    assert sitekey_from_iframe_src(
        "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2/av0/"
        "rcv/abc/0x4AAAAAAABkMYinukE8nzY/auto/fbE/new/normal/auto/"
    ) == (TURNSTILE, "0x4AAAAAAABkMYinukE8nzY", False)
    assert sitekey_from_iframe_src("https://example.com/frame") is None
