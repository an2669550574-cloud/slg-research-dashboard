"""LLM 预算记账汇总 llm_budget：验证日 / 月成本跨三个「用户可触发」端点
（素材分析 / 标签分析 / 创意迁移）汇总。

修历史 bug：预算闸门 today_cost_usd 曾只统计 materials.analysis_cost_usd 一张表，
创意迁移 / 标签分析的花费记在各自表里、不进闸门——「三端点共享日预算」在记账层是漏的。
"""
from datetime import date, timedelta

import pytest


async def _seed_material(cost, analyzed_at):
    from app.database import AsyncSessionLocal
    from app.models.material import Material
    async with AsyncSessionLocal() as db:
        db.add(Material(app_id="com.x.slg", title="素材A", source="upload",
                        analysis_cost_usd=cost, analyzed_at=analyzed_at))
        await db.commit()


async def _seed_tag_msg(cost, created_at):
    from app.database import AsyncSessionLocal
    from app.models.tag_analysis import TagAnalysisMessage, TagAnalysisSession
    async with AsyncSessionLocal() as db:
        s = TagAnalysisSession(title="标签分析会话", model="gemini-3-flash-preview")
        db.add(s)
        await db.flush()
        db.add(TagAnalysisMessage(session_id=s.id, role="assistant", content="分析报告正文",
                                  cost_usd=cost, created_at=created_at))
        await db.commit()


async def _seed_creative(cost, script_cost, created_at, script_updated_at):
    from app.database import AsyncSessionLocal
    from app.models.material import CreativeAdaptation, Material
    async with AsyncSessionLocal() as db:
        m = Material(app_id="com.y.slg", title="母素材", source="upload")
        db.add(m)
        await db.flush()
        db.add(CreativeAdaptation(material_id=m.id, our_product="无尽火线 brief 全文",
                                  cost_usd=cost, script_cost_usd=script_cost,
                                  created_at=created_at, script_updated_at=script_updated_at))
        await db.commit()


@pytest.mark.asyncio
async def test_day_cost_sums_all_three_sources(client):
    """核心：当日成本 = 素材分析 + 标签分析 + 创意迁移（方向 + 脚本）四列全计入。
    旧实现只算 materials.analysis_cost_usd → 会把结果算成 1.0（漏 tag/creative）。"""
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.services import llm_budget

    now = utcnow_naive()
    await _seed_material(1.0, now)
    await _seed_tag_msg(2.0, now)
    await _seed_creative(3.0, 4.0, now, now)

    async with AsyncSessionLocal() as db:
        day = await llm_budget.day_cost_usd(db)
        month = await llm_budget.month_cost_usd(db)
    assert day == pytest.approx(10.0)    # 1+2+3+4；非 1.0（旧 bug 只算 materials）
    assert month == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_today_cost_usd_delegates_to_aggregate(client):
    """video_analyze.today_cost_usd（6 处 router 闸门的入口）委托 llm_budget 后
    应等于三表汇总——保证既有闸门自动受益于修复。"""
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.services import video_analyze

    now = utcnow_naive()
    await _seed_material(1.5, now)
    await _seed_tag_msg(2.5, now)

    async with AsyncSessionLocal() as db:
        spent = await video_analyze.today_cost_usd(db)
    assert spent == pytest.approx(4.0)   # 非 1.5


@pytest.mark.asyncio
async def test_day_excludes_prior_days_and_month_boundary(client):
    """日边界：远古行不进当日也不进当月；月边界：本月早些天的行进当月、不进当日。"""
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.services import llm_budget

    now = utcnow_naive()
    await _seed_material(5.0, now)                        # 今天
    await _seed_material(9.0, now - timedelta(days=40))   # 40 天前 → 跨月，日/月都排除

    async with AsyncSessionLocal() as db:
        day = await llm_budget.day_cost_usd(db)
        month = await llm_budget.month_cost_usd(db)
    assert day == pytest.approx(5.0)
    assert month == pytest.approx(5.0)

    # 本月内、今天之前的行：进当月、不进当日（月初 1 号该区间为空，跳过以免 flaky）
    if date.today().day > 1:
        earlier_this_month = now.replace(day=1, hour=0, minute=5, second=0, microsecond=0)
        await _seed_tag_msg(7.0, earlier_this_month)
        async with AsyncSessionLocal() as db:
            day2 = await llm_budget.day_cost_usd(db)
            month2 = await llm_budget.month_cost_usd(db)
        assert day2 == pytest.approx(5.0)     # 月初那条不进当日
        assert month2 == pytest.approx(12.0)  # 5 + 7 进当月
