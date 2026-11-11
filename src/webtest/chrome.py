"""Locate a real Google Chrome binary.

Real Chrome (not the bundled Chromium) is the patchright recommendation: Chromium
ships a different set of codecs, brands and features, and those differences are
fingerprintable. The snap Chromium is excluded on purpose: snap confinement
breaks persistent profiles outside ~/snap and remote debugging.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# Where scripts/install_chrome.sh unpacks Chrome when there is no sudo.
LOCAL_CHROME = Path("~/.local/opt/google-chrome/opt/google/chrome/chrome").expanduser()

CANDIDATES: tuple[Path, ...] = (
    LOCAL_CHROME,
    Path("/opt/google/chrome/chrome"),
)

PATH_NAMES = ("google-chrome-stable", "google-chrome")


def find_chrome(explicit: Path | None = None) -> Path | None:
    """Explicit path first, then known install locations, then $PATH. None if absent."""
    if explicit is not None:
        if not _is_exe(explicit):
            raise FileNotFoundError(f"WEBTEST_CHROME_PATH is not an executable: {explicit}")
        return explicit
    for candidate in CANDIDATES:
        if _is_exe(candidate):
            return candidate
    for name in PATH_NAMES:
        found = shutil.which(name)
        if found and "/snap/" not in found:
            return Path(found)
    return None


def chrome_version(path: Path) -> str | None:
    try:
        out = subprocess.run(
            [str(path), "--version"], check=False, capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def _is_exe(path: Path) -> bool:
    return path.is_file() and path.stat().st_mode & 0o111 != 0
