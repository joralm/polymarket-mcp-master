import logging
import re
import sys

import structlog

from .config import Settings


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return re.sub(r"(?i)(?:0x)?[0-9a-f]{64}", "[redacted]", super().format(record))


def sanitize(value: object) -> object:
    if isinstance(value, str):
        return re.sub(r"(?i)(?:0x)?[0-9a-f]{64}", "[redacted]", value)[:512]
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value[:100]]
    if isinstance(value, dict):
        return {
            k: "[redacted]" if any(x in str(k).lower() for x in ("key", "secret", "password")) else sanitize(v)
            for k, v in value.items()
        }
    return value


def sanitize_event(_logger: object, _method: str, event: dict) -> dict:
    return sanitize(event)


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        stream=sys.stderr, level=settings.log_level, format="%(message)s", force=True
    )
    for handler in logging.getLogger().handlers:
        handler.setFormatter(RedactingFormatter("%(message)s"))
    renderer = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.stdlib.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            sanitize_event,
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
