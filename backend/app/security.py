from fastapi import Header, HTTPException, status
from typing import Optional
from app.config import settings


async def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """API Key 鉴权依赖。

    - settings.API_KEY 未配置 → 跳过（兼容本地开发）
    - 已配置 → 请求头 X-API-Key 必须匹配
    """
    expected = settings.API_KEY
    if not expected:
        return
    if not x_api_key or x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
