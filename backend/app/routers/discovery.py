"""发现层端点。走全局 API_KEY 鉴权（main.py _protected）。
- POST /triage      切片1：只读分诊（覆盖核查 + 零 ST 溯源 + 建档草稿）
- POST /log         期2 出口B：人工确认 → 写 discovery 影子行 → 次日进维护者卡【📮 发现层线报】
- POST /build-entity 期2.5 出口A：人工确认 → 一键建主体 + pin + 挂开发者账号雷达
"""
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.discovery_triage import triage, log_tip, build_entity_from_tip
from app.services.discovery_wechat import scan as scan_wechat

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


class TriageIn(BaseModel):
    tip: str                    # GP 包名 / iOS 数字 id / 商店链接
    dry_run: bool = True        # 前向兼容；/triage 恒只读


class BuildEntityIn(BaseModel):
    tip: str
    name: Optional[str] = None       # 覆盖草稿厂商名（反解不出时必填）
    is_slg: Optional[bool] = None    # 覆盖草稿 is_slg
    hq_region: Optional[str] = None
    brief: Optional[str] = None


@router.post("/triage")
async def triage_tip(body: TriageIn) -> dict:
    """线报 → 覆盖核查 + 零 ST 溯源 + 建档草稿（只读，不落库）。"""
    return await triage(body.tip, dry_run=body.dry_run)


@router.post("/log")
async def log_discovery_tip(body: TriageIn) -> dict:
    """出口B：人工确认未追踪线报 → 写 discovery 影子行（次日进维护者卡）。"""
    return await log_tip(body.tip)


@router.post("/build-entity")
async def build_entity(body: BuildEntityIn) -> dict:
    """出口A：人工确认未追踪线报 → 建 PublisherEntity + pin + 挂开发者账号雷达。"""
    return await build_entity_from_tip(body.tip, name=body.name, is_slg=body.is_slg,
                                       hq_region=body.hq_region, brief=body.brief)


class ScanWechatIn(BaseModel):
    days: int = 3            # 近 N 天文章
    per_account: int = 5     # 每号取最近几篇


@router.post("/scan-wechat")
async def scan_wechat_sources(body: ScanWechatIn) -> dict:
    """期5a（只读）：扫发现源公众号最近文 → LLM 抽 SLG 新品 → 名→商店反解 → 覆盖核查 → 候选表。
    人工核 unknown 候选后走 /build-entity 或 /log。session 挂则探活门控空返。"""
    return await scan_wechat(days=body.days, per_account=body.per_account)
