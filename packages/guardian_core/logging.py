"""Structlog configuration for Guardian services."""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.stdlib import BoundLogger


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog to emit JSON logs to stdout."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> BoundLogger:
    """Return a configured structlog logger."""
    logger: BoundLogger = structlog.get_logger(name)
    return logger
