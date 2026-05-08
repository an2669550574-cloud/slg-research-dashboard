from fastapi import APIRouter

from app.services import quota

router = APIRouter(prefix="/api/quota", tags=["quota"])


@router.get("/")
async def get_quota():
    """当月 Sensor Tower API 用量；前端 dashboard 顶部展示。"""
    return await quota.current_usage()
