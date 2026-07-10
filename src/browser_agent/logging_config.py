"""Centralised loguru configuration for ``browser_agent``.

A single stderr sink with a consistent format, plus a stdlib
``logging`` interceptor so third-party libraries (``httpx``,
``pydantic_ai``, …) show up in the same stream.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from loguru import logger

_CHATTY_LOGGERS: dict[str, str] = {
    "httpx": "WARNING",
    "httpcore": "WARNING",
    "openai": "WARNING",
    "urllib3": "WARNING",
}


class InterceptHandler(logging.Handler):
    """Route stdlib ``logging`` records into :mod:`loguru`."""

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 0
        while frame and depth < 20:
            filename = frame.f_code.co_filename
            if filename == logging.__file__:
                break
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def _apply_quiet_overrides() -> None:
    for name, level in _CHATTY_LOGGERS.items():
        logging.getLogger(name).setLevel(level)


def _install_intercept() -> None:
    handler = InterceptHandler()
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def configure_logging() -> None:
    """Idempotent loguru setup for the whole package."""
    logger.remove()
    logger.add(sys.stderr, format=_LOG_FORMAT, level=_log_level(), colorize=_use_color())
    _maybe_add_file_handler()
    _install_intercept()
    _apply_quiet_overrides()


_LOG_FORMAT = (
    "<green>{time:HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def _log_level() -> str:
    return os.environ.get("BROWSER_AGENT_LOG_LEVEL", "INFO").upper()


def _use_color() -> bool:
    return os.environ.get("BROWSER_AGENT_LOG_NO_COLOR", "").lower() not in {"1", "true", "yes"}


def _maybe_add_file_handler() -> None:
    log_file = os.environ.get("BROWSER_AGENT_LOG_FILE")
    if not log_file:
        return
    logger.add(log_file, rotation="10 MB", retention=5, format=_LOG_FORMAT, level="INFO")
