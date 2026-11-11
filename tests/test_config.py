from pathlib import Path

import pytest

from webtest.browser import _proxy
from webtest.config import BrowserConfig


def test_defaults_from_empty_env():
    cfg = BrowserConfig.from_env({})
    assert cfg == BrowserConfig()
    assert cfg.headless is False


def test_values_from_env():
    cfg = BrowserConfig.from_env(
        {
            "WEBTEST_HEADLESS": "yes",
            "WEBTEST_PROFILE_DIR": "/tmp/p",
            "WEBTEST_PROXY": "  ",
            "WEBTEST_TIMEOUT_MS": "5000",
        }
    )
    assert cfg.headless is True
    assert cfg.profile_dir == Path("/tmp/p")
    assert cfg.proxy is None
    assert cfg.timeout_ms == 5000


def test_bad_boolean_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        BrowserConfig.from_env({"WEBTEST_HEADLESS": "maybe"})


def test_proxy_credentials_are_split_out():
    assert _proxy("http://u:p@1.2.3.4:8080") == {
        "server": "http://1.2.3.4:8080",
        "username": "u",
        "password": "p",
    }
    assert _proxy("socks5://host:1080") == {"server": "socks5://host:1080"}
