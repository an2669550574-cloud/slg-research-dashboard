"""发现层端点。切片1：人工线报快速分诊（只读）。走全局 API_KEY 鉴权（main.py _protected）。"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.services.discovery_triage import triage

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


class TriageIn(BaseModel):
    tip: str                    # GP 包名 / iOS 数字 id / 商店链接
    dry_run: bool = True        # 前向兼容；切片1 恒只读


@router.post("/triage")
async def triage_tip(body: TriageIn) -> dict:
    """线报 → 覆盖核查 + 零 ST 溯源 + 建档草稿（只读，不落库）。"""
    return await triage(body.tip, dry_run=body.dry_run)
