"""限流：基于 slowapi（Flask-Limiter 的 starlette 移植）。

策略：
- 默认 Key 用客户端 IP；带 X-API-Key 时改用 API Key（同 key 多 IP 共享配额）
- 未配置 RATE_LIMIT_DEFAULT 时关闭整体限流；只对特别贵的端点（AI 同步）保留固定限制
- Cooldown：极简的"最少间隔"开关（per-key），用于守护强制刷新这类直接消耗
  Sensor Tower 月度配额的端点。客户端 disable 按钮容易被刷新 tab / 多窗口绕过，
  必须有服务端兜底。
"""
import time
from typing import Optional
from fastapi import HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
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


class CooldownGuard:
    """Per-key 最少间隔守护。命中冷却返回 429 + Retry-After。

    单进程内存实现；多副本需共享存储（Redis 等）。和 slowapi 解耦——slowapi
    在 RATE_LIMIT_DEFAULT 未设置时整体禁用，但本守护始终生效。
    """

    def __init__(self, seconds: float, name: str):
        self.seconds = seconds
        self.name = name
        self._last: dict[str, float] = {}

    def __call__(self, request: Request) -> None:
        bucket = f"{self.name}:{_key(request)}"
        now = time.monotonic()
        last = self._last.get(bucket)
        if last is not None and now - last < self.seconds:
            retry_after = max(1, int(self.seconds - (now - last) + 0.5))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {retry_after}s before refreshing again",
                headers={"Retry-After": str(retry_after)},
            )
        self._last[bucket] = now


# 强制刷新今日榜单的服务端冷却：30s 内同一 key 只允许一次（消耗一次 Sensor Tower 配额）
refresh_cooldown = CooldownGuard(seconds=30.0, name="refresh_rankings")
