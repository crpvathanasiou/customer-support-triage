import logging
from typing import Any

from fastapi import FastAPI
from redis import Redis

from app.core.settings import get_settings

settings = get_settings()


def setup_logging() -> None:
    """
    Logs to stdout/stderr (container-friendly).
    """
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


setup_logging()
logger = logging.getLogger("Customer Support Triage")


def check_redis_connection(redis_url: str) -> bool:
    """
    Optional Redis ping to demonstrate dependency checks.
    """
    try:
        client = Redis.from_url(
            redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            decode_responses=True,
        )
        return bool(client.ping())
    except Exception:
        return False


# IMPORTANT:
# Uvicorn loads "app.main:app" by importing app.main and then looking for a variable named "app".
# This must exist at module import time.
app = FastAPI(
    title="Customer Support Triage",
    version=settings.app_version,
)


@app.get("/health")
def health() -> dict[str, Any]:
    redis_ok = None
    if settings.redis_url:
        redis_ok = check_redis_connection(settings.redis_url)

    return {
        "status": "ok",
        "app_env": settings.app_env,
        "app_version": settings.app_version,
        "redis_connected": redis_ok,
    }


@app.get("/version")
def version() -> dict[str, str]:
    return {"version": settings.app_version}
