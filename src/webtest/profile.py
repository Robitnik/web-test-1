"""Profile guard: one profile directory = one running Chrome.

Two Chrome processes on the same user-data-dir corrupt it (cookies, Local State).
Chrome marks a live profile with a `SingletonLock` symlink whose target is
`<hostname>-<pid>`. A live lock is a hard stop; a stale one (dead pid, e.g.
after a crash) is reported and left for Chrome itself to clean up.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path


class ProfileInUse(RuntimeError):
    pass


def lock_owner(profile_dir: Path) -> tuple[str, int] | None:
    """(hostname, pid) from SingletonLock, or None when there is no readable lock."""
    lock = profile_dir / "SingletonLock"
    try:
        target = os.readlink(lock)
    except OSError:
        return None
    host, _, pid = target.rpartition("-")
    if not host or not pid.isdigit():
        return None
    return host, int(pid)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def assert_free(profile_dir: Path) -> None:
    owner = lock_owner(profile_dir)
    if owner is None:
        return
    host, pid = owner
    if host == socket.gethostname() and _pid_alive(pid):
        raise ProfileInUse(
            f"profile {profile_dir} is already open by Chrome pid {pid}. "
            "Close that window or use another WEBTEST_PROFILE_DIR. "
            "Do not delete SingletonLock while that process is alive."
        )
