"""Sentry 接入与深度健康检查。

Sentry：未配置 DSN 时整段 noop，不引入 import 副作用。
"""
import asyncio
import logging
from typing import Optional
from app.config import settings

logger = logging.getLogger(__name__)


def init_sentry() -> None:
    if not settings.SENTRY_DSN:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; skipping")
        return

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        # 不上报请求体（可能含 API key / 业务数据）
        send_default_pii=False,
    )
    logger.info("Sentry initialized (env=%s)", settings.SENTRY_ENVIRONMENT)


async def check_db() -> dict:
    from app.database import engine
    from sqlalchemy import text
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)[:200]}


async def check_sensor_tower() -> dict:
    """对外探测：Sensor Tower 域名 TCP/HTTP 是否可达。mock 模式直接返回 skip。"""
    if settings.USE_MOCK_DATA or not settings.SENSOR_TOWER_API_KEY:
        return {"status": "skipped", "reason": "mock mode"}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            # 任何 2xx/4xx 都算可达；5xx 才是真不可达
            r = await client.get(f"{settings.SENSOR_TOWER_BASE_URL}/")
            return {"status": "ok" if r.status_code < 500 else "degraded", "code": r.status_code}
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        return {"status": "unreachable", "error": str(e)[:200]}


async def check_anthropic() -> dict:
    if not settings.ANTHROPIC_API_KEY:
        return {"status": "skipped", "reason": "no api key"}
    # Anthropic SDK 没有便宜的 ping，直接走 messages 又昂贵。这里只做 DNS 探测。
    import socket
    try:
        await asyncio.get_running_loop().run_in_executor(None, socket.gethostbyname, "api.anthropic.com")
        return {"status": "ok"}
    except OSError as e:
        return {"status": "unreachable", "error": str(e)[:200]}


async def deep_health() -> dict:
    db, st, an = await asyncio.gather(check_db(), check_sensor_tower(), check_anthropic())
    overall = "ok" if db["status"] == "ok" else "degraded"
    return {
        "status": overall,
        "checks": {"database": db, "sensor_tower": st, "anthropic": an},
    }
