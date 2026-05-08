"""限流：基于 slowapi（Flask-Limiter 的 starlette 移植）。

策略：
- 默认 Key 用客户端 IP；带 X-API-Key 时改用 API Key（同 key 多 IP 共享配额）
- 未配置 RATE_LIMIT_DEFAULT 时关闭整体限流；只对特别贵的端点（AI 同步）保留固定限制
"""
from typing import Optional
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request
from app.config import settings


def _key(request: Request) -> str:
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"key:{api_key}"
    return f"ip:{get_remote_address(request)}"


def _default_limits() -> Optional[list[str]]:
    if not settings.RATE_LIMIT_DEFAULT:
        return None
    return [settings.RATE_LIMIT_DEFAULT]


limiter = Limiter(
    key_func=_key,
    default_limits=_default_limits() or [],
    headers_enabled=True,
    enabled=bool(settings.RATE_LIMIT_DEFAULT),
)
