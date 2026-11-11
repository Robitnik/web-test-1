"""File logging for long runs: one folder, one file per concern.

    logs/errors.log       ERROR+ from everywhere, with tracebacks (code crashes)
    logs/captcha.log      the `webtest.captcha` loggers: API tasks, solve failures
    logs/submissions.log  the `submissions` logger: one line per attempt and result
    logs/all.log          everything at INFO+, for reading a run end to end

Files rotate at 2 MB (5 kept). Calling setup_logging twice is harmless.
"""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_MARK = "_webtest_logsetup"


def _handler(path: Path, level: int, only: str | None = None) -> logging.Handler:
    h = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    h.setLevel(level)
    h.setFormatter(logging.Formatter(FMT))
    if only:
        h.addFilter(logging.Filter(only))
    setattr(h, _MARK, True)
    return h


def setup_logging(log_dir: str | Path = "logs") -> Path:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, _MARK, False)]:
        root.removeHandler(h)
        h.close()
    root.setLevel(logging.INFO)
    root.addHandler(_handler(log_dir / "errors.log", logging.ERROR))
    root.addHandler(_handler(log_dir / "captcha.log", logging.INFO, "webtest.captcha"))
    root.addHandler(_handler(log_dir / "submissions.log", logging.INFO, "submissions"))
    root.addHandler(_handler(log_dir / "all.log", logging.INFO))

    crash = logging.getLogger("crash")

    def excepthook(tp, val, tb):
        if not issubclass(tp, KeyboardInterrupt):
            crash.critical("uncaught exception", exc_info=(tp, val, tb))
        sys.__excepthook__(tp, val, tb)

    def thread_hook(args):
        crash.critical("uncaught exception in thread %s", args.thread,
                       exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
    return log_dir
