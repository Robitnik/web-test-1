"""Browser configuration, read from the environment (.env supported)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"not a boolean: {raw!r}")


def _opt(raw: str | None) -> str | None:
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


@dataclass(frozen=True)
class BrowserConfig:
    profile_dir: Path = Path(".profiles/default")
    headless: bool = False
    # Explicit Chrome binary. None = auto-detect via webtest.chrome.find_chrome().
    chrome_path: Path | None = None
    proxy: str | None = None
    locale: str | None = None
    timezone_id: str | None = None
    slow_mo_ms: int = 0
    timeout_ms: int = 30_000
    downloads_dir: Path = Path("downloads")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> BrowserConfig:
        if env is None:
            from dotenv import load_dotenv

            load_dotenv()
            env = os.environ
        d = cls()
        chrome = _opt(env.get("WEBTEST_CHROME_PATH"))
        return cls(
            profile_dir=Path(_opt(env.get("WEBTEST_PROFILE_DIR")) or d.profile_dir),
            headless=_bool(env.get("WEBTEST_HEADLESS"), d.headless),
            chrome_path=Path(chrome).expanduser() if chrome else None,
            proxy=_opt(env.get("WEBTEST_PROXY")),
            locale=_opt(env.get("WEBTEST_LOCALE")),
            timezone_id=_opt(env.get("WEBTEST_TIMEZONE")),
            slow_mo_ms=int(_opt(env.get("WEBTEST_SLOW_MO_MS")) or d.slow_mo_ms),
            timeout_ms=int(_opt(env.get("WEBTEST_TIMEOUT_MS")) or d.timeout_ms),
            downloads_dir=Path(_opt(env.get("WEBTEST_DOWNLOADS_DIR")) or d.downloads_dir),
        )

    def with_(self, **changes) -> BrowserConfig:
        return replace(self, **changes)
