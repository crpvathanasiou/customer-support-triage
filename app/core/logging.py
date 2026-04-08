import logging
from typing import Any

from app.core.settings import get_settings


def configure_logging() -> None:
    settings = get_settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def bind_log_context(**kwargs: Any) -> dict[str, Any]:
    """
    Simple helper for structured context payloads.
    """
    return {k: v for k, v in kwargs.items() if v is not None}
