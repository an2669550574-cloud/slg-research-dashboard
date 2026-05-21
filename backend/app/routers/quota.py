from fastapi import APIRouter, Query

from app.services import quota

router = APIRouter(prefix="/api/quota", tags=["quota"])


@router.get("/")
async def get_quota():
    """当月 Sensor Tower API 用量；前端 dashboard 顶部展示。"""
    return await quota.current_usage()


@router.get("/history")
async def get_quota_history(days: int = Query(30, ge=1, le=180)):
    """近 N 个 UTC 日的本项目调用次数;前端画"每日烧得快不快"折线图。
    缺失日填 0(包含未上线 daily 计数的远古日)。仅本项目计数,不含公司池。"""
    return {"days": days, "points": await quota.usage_history(days)}
