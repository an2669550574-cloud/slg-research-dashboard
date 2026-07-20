"""LLM 花费统计：跨三个「用户可触发」端点汇总成本，供日 / 月预算护栏用。

历史坑（本模块修正的对象）：预算闸门 `video_analyze.today_cost_usd` 曾只统计
`materials.analysis_cost_usd` 一张表，而创意迁移（`creative_adaptations`）和标签分析
（`tag_analysis_messages`）的花费记在各自表里、**不进闸门**——代码到处写「三端点共享
LLM_DAILY_BUDGET_USD 日预算」，在记账层其实是漏的（只算了三分之一）。本模块汇总三表
（四个成本列）修正之。

成本三源（时间列均 utcnow_naive / UTC naive，口径一致）：
  - materials.analysis_cost_usd        ← analyzed_at        （素材视频分析）
  - tag_analysis_messages.cost_usd     ← created_at         （标签分析对话）
  - creative_adaptations.cost_usd      ← created_at         （创意迁移·方向生成）
  - creative_adaptations.script_cost_usd ← script_updated_at（创意迁移·脚本后补，可跨天）

日 / 月边界按 **UTC** 切（`utcnow_naive()`），与上面四个时间列同一口径。原先用
`date.today()`（机器本地日），靠「生产容器为 UTC 故等价」这个隐含前提成立——在 UTC+N 的机器上，
本地每天 00:00–N:00 这段时间本地日已翻页而 UTC 未翻，时间锚落到未来，当日花费恒算成 0，
**闸门静默失效且不报错**（2026-07-20 在 UTC+8 开发机上实测到，测试全绿的 CI 跑 UTC 故看不见）。
"""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow_naive
from app.models.material import CreativeAdaptation, Material
from app.models.tag_analysis import TagAnalysisMessage


async def _sum_since(db: AsyncSession, column, time_column, since: datetime) -> float:
    """单列成本自 since 起求和（NULL 计 0）。"""
    stmt = select(func.coalesce(func.sum(column), 0.0)).where(time_column >= since)
    return float((await db.execute(stmt)).scalar_one() or 0.0)


async def cost_since(db: AsyncSession, since: datetime) -> float:
    """自 since 起三端点 LLM 总花费（美元）。"""
    return (
        await _sum_since(db, Material.analysis_cost_usd, Material.analyzed_at, since)
        + await _sum_since(db, TagAnalysisMessage.cost_usd, TagAnalysisMessage.created_at, since)
        + await _sum_since(db, CreativeAdaptation.cost_usd, CreativeAdaptation.created_at, since)
        + await _sum_since(
            db, CreativeAdaptation.script_cost_usd, CreativeAdaptation.script_updated_at, since
        )
    )


def _day_start() -> datetime:
    """当日起点（**UTC**）。必须与成本表的时间列同口径，否则时间锚会落到未来、当日花费恒 0。"""
    t = utcnow_naive()
    return datetime(t.year, t.month, t.day)


def _month_start() -> datetime:
    """当月起点（**UTC**），同 _day_start。"""
    t = utcnow_naive()
    return datetime(t.year, t.month, 1)


async def day_cost_usd(db: AsyncSession) -> float:
    """当日三端点 LLM 总花费（美元）。"""
    return await cost_since(db, _day_start())


async def month_cost_usd(db: AsyncSession) -> float:
    """当月三端点 LLM 总花费（美元）。"""
    return await cost_since(db, _month_start())
