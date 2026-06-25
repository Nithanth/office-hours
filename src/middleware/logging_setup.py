"""Structured logging configuration using Loguru.

Configures Loguru sinks, bridges stdlib logging, and silences noisy
third-party loggers. Should be called once at application startup
(before any other code emits logs).
"""

import logging
import sys

from loguru import logger

from config import config


# ---------------------------------------------------------------------------
# Intercept handler: forwards stdlib logging → Loguru
# ---------------------------------------------------------------------------


class _InterceptHandler(logging.Handler):
    """Forward standard ``logging`` records to Loguru for uniform formatting."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level_name = logger.level(record.levelname).name
        except ValueError:
            level_name = str(record.levelno)
        logger_opt = logger.opt(depth=6, exception=record.exc_info)
        logger_opt.log(level_name, record.getMessage())


# ---------------------------------------------------------------------------
# SSE access-log noise filter
# ---------------------------------------------------------------------------


class _DropSSE204Filter(logging.Filter):
    """Filter out noisy 204 responses from SSE keepalive endpoints."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not (" 204 " in msg and "/report/stream" in msg)


# ---------------------------------------------------------------------------
# Public setup function
# ---------------------------------------------------------------------------


def configure_logging() -> None:
    """Set up Loguru sinks, intercept handler, and silence noisy loggers.

    Safe to call multiple times (idempotent).
    """
    # Bridge stdlib logging through Loguru
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)

    # Configure Loguru sinks
    logger.remove()
    logger.add(
        sys.stderr,
        level=config.LOG_LEVEL.upper(),
        serialize=config.LOG_FORMAT_JSON,
    )

    # Optional persistent log file
    if config.LOG_FILE:
        logger.add(
            config.LOG_FILE,
            level=config.LOG_LEVEL.upper(),
            serialize=config.LOG_FORMAT_JSON,
            rotation="20 MB",
            compression="gz",
            enqueue=True,
            backtrace=True,
        )

    # Filter noisy SSE access logs
    logging.getLogger("uvicorn.access").addFilter(_DropSSE204Filter())

    # Silence noisy third-party loggers
    for name in (
        "pdfminer",
        "pdfminer.pdfinterp",
        "pdfminer.cmapdb",
        "pdfminer.pdfpage",
    ):
        logging.getLogger(name).setLevel(logging.ERROR)
    for name in ("google.genai", "google.genai.types", "google_genai"):
        logging.getLogger(name).setLevel(logging.ERROR)
