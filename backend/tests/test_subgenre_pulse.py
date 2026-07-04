"""赛道脉搏 /newcomers/subgenre-pulse（近 N 天新品按子品类分布 + 环比，P1-2 stretch）。

验收：
- 按 app_id 去重（同 app 跨 combo 多行算一个），用最早检出定窗口
- 当前窗口 vs 上一等长窗口环比 delta；超出 2×window 的老行不计
- 忽略名单过滤；无 subgenre 的行不计
- 中文夹具（CJK 纪律）
"""
from datetime import timedelta

import pytest

SLG_PUB = "Century Games Pte. Ltd."
IGN_PUB = "麻将工作室"


async def _seed(app_id, subgenre, days_ago, publisher=SLG_PUB, country="US"):
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.newcomer import MarketNewcomerLog
    async with AsyncSessionLocal() as db:
        db.add(MarketNewcomerLog(
            country=country, platform="ios", app_id=app_id, as_of="2026-06-01",
            name=f"游戏{app_id}", publisher=publisher, subgenre_cn=subgenre,
            first_detected_at=utcnow_naive() - timedelta(days=days_ago)))
        await db.commit()


@pytest.mark.asyncio
async def test_subgenre_pulse_windows_dedup_and_delta(client):
    # 当前窗口（≤30 天）
    await _seed("a1", "数字门SLG", 5)
    await _seed("a1", "数字门SLG", 3, country="JP")   # 同 app 第二 combo → 去重，min=5d 仍落当前
    await _seed("a3", "基地建设SLG", 10)
    # 上一窗口（30~60 天）
    await _seed("a2", "数字门SLG", 40)
    # 忽略名单 + 无 subgenre + 超窗
    from app.database import AsyncSessionLocal
    from app.models.publisher import PublisherIgnore
    async with AsyncSessionLocal() as db:
        db.add(PublisherIgnore(kind="app_id", value="ign", label="麻将"))   # app_id 粒度忽略
        await db.commit()
    await _seed("ign", "塔防", 5, publisher=IGN_PUB)   # 忽略名单 → 剔除
    await _seed("old", "国战SLG", 70)                   # 超 2×30=60 → 不计

    resp = await client.get("/api/newcomers/subgenre-pulse?days=30")
    body = resp.json()
    assert body["days"] == 30
    assert body["total"] == 2          # 当前窗口去重后：a1 + a3
    by_sg = {b["subgenre"]: b for b in body["buckets"]}
    assert by_sg["数字门SLG"]["count"] == 1 and by_sg["数字门SLG"]["prev_count"] == 1
    assert by_sg["数字门SLG"]["delta"] == 0
    assert by_sg["基地建设SLG"]["count"] == 1 and by_sg["基地建设SLG"]["prev_count"] == 0
    assert by_sg["基地建设SLG"]["delta"] == 1
    assert "塔防" not in by_sg and "国战SLG" not in by_sg   # 忽略 / 超窗
    # 排序：同 count 按 delta 降序 → 基地建设(delta1) 在 数字门(delta0) 前
    assert [b["subgenre"] for b in body["buckets"]] == ["基地建设SLG", "数字门SLG"]


@pytest.mark.asyncio
async def test_subgenre_pulse_empty(client):
    resp = await client.get("/api/newcomers/subgenre-pulse?days=30")
    body = resp.json()
    assert body["total"] == 0 and body["buckets"] == []
