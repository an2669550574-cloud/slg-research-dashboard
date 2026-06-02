from fastapi import Header, HTTPException, status
from typing import Optional
from app.config import settings


async def require_admin_password(
    x_admin_password: Optional[str] = Header(default=None),
) -> None:
    """标签库「删除」专用管理员口令依赖（方案 b：无用户体系，单口令挡误删）。

    - settings.ADMIN_DELETE_PASSWORD 未配置 → 跳过（兼容本地开发，同 require_api_key）
    - 已配置 → 请求头 X-Admin-Password 必须匹配，否则 403
    """
    expected = settings.ADMIN_DELETE_PASSWORD
    if not expected:
        return
    if not x_admin_password or x_admin_password != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="删除标签需要管理员口令",
        )


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
