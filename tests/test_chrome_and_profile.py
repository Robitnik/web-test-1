import os
import socket

import pytest

from webtest.chrome import find_chrome
from webtest.profile import ProfileInUse, assert_free, lock_owner


def test_explicit_chrome_must_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        find_chrome(tmp_path / "nope")


def test_explicit_chrome_wins(tmp_path):
    exe = tmp_path / "chrome"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    assert find_chrome(exe) == exe


def test_no_lock_is_free(tmp_path):
    assert lock_owner(tmp_path) is None
    assert_free(tmp_path)


def test_live_lock_blocks(tmp_path):
    os.symlink(f"{socket.gethostname()}-{os.getpid()}", tmp_path / "SingletonLock")
    with pytest.raises(ProfileInUse):
        assert_free(tmp_path)


def test_stale_lock_does_not_block(tmp_path):
    os.symlink(f"{socket.gethostname()}-999999999", tmp_path / "SingletonLock")
    assert_free(tmp_path)
