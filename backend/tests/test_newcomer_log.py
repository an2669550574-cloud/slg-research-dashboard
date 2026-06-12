"""新面孔检出沉淀（market_newcomer_log，新品监测 v2）。

核心验证：
- record：检出落库 + 富化字段写入；同 combo×app 唯一，重跑不重写（首报定格）
- /api/newcomers/history：时间窗 / 市场 / 平台 / topn 筛选；screenshots 解 JSON
- 富化失败留 NULL 不丢检出；中文夹具（CJK 纪律）
"""
import importlib
import json
from datetime import timedelta

import pytest


def _live(mod):
    """conftest 每 test 清 sys.modules——顶层 import 会拿到指向旧 engine 的过期模块，
    必须用 importlib 取活模块（见 project_shipped_history 持久 gotcha）。"""
    return importlib.import_module(mod)


async def _seed_rankings(today, prev, country, prefix):
    """country 与 app_id 前缀按用例隔离——测试库跨文件共享，复用夹具会撞唯一约束。"""
    database = _live("app.database")
    GameRanking = _live("app.models.game").GameRanking
    async with database.AsyncSessionLocal() as db:
        rows = [
            (f"{prefix}veteran", prev, 1), (f"{prefix}veteran", today, 1),
            (f"{prefix}rookie01", today, 4),   # 新面孔（Top50 内）
            (f"{prefix}rookie99", today, 88),  # 新面孔（50<rank<=100，历史口径收、Top50 筛选排除）
        ]
        for app_id, date, rank in rows:
            db.add(GameRanking(app_id=app_id, date=date, rank=rank, downloads=None,
                               revenue=12345.0 if app_id.endswith("rookie01") else None,
                               country=country, platform="ios",
                               name=f"测试游戏{app_id}", publisher="神秘工作室", icon_url=None))
        await db.commit()


@pytest.mark.asyncio
async def test_record_and_history_endpoint(client, monkeypatch):
    nl = importlib.import_module("app.services.newcomer_log")
    now = _live("app.database").utcnow_naive()
    today = now.strftime("%Y-%m-%d")
    prev = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    await _seed_rankings(today, prev, "DE", "a_")

    async def fake_enrich(app_id, country, platform):
        if app_id == "a_rookie01":
            return {"store_url": "https://apps.apple.com/us/app/id1", "genre": "Strategy",
                    "rating": 4.5, "rating_count": 100, "price": "Free",
                    "description": "中文描述：史诗策略大作。",
                    "screenshot_urls": json.dumps(["https://x/1.jpg"]),
                    "release_date": "2026-06-01", "enrich_source": "itunes"}
        return None  # rookie99 富化失败 → 留 NULL 不丢检出

    monkeypatch.setattr(nl.settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(nl, "enrich_fields", fake_enrich)
    monkeypatch.setattr(nl, "_POLITE_DELAY_S", 0)

    r1 = await nl.record_market_newcomers("DE", "ios")
    assert r1 == {"detected": 2, "recorded": 2, "enriched": 1}
    # 幂等：重跑不重写
    r2 = await nl.record_market_newcomers("DE", "ios")
    assert r2["recorded"] == 0

    resp = await client.get("/api/newcomers/history?days=7&country=DE")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    by_id = {i["app_id"]: i for i in items}
    rich = by_id["a_rookie01"]
    assert rich["genre"] == "Strategy" and rich["screenshots"] == ["https://x/1.jpg"]
    assert rich["name"] == "测试游戏a_rookie01" and rich["publisher"] == "神秘工作室"
    assert rich["country"] == "DE" and rich["platform"] == "ios"
    poor = by_id["a_rookie99"]
    assert poor["genre"] is None and poor["enrich_source"] is None  # 失败留 NULL

    # topn=50 筛掉 88 名的检出
    resp = await client.get("/api/newcomers/history?days=7&topn=50&country=DE")
    assert [i["app_id"] for i in resp.json()["items"]] == ["a_rookie01"]
    # 平台/市场筛选
    resp = await client.get("/api/newcomers/history?days=7&platform=android&country=DE")
    assert resp.json()["items"] == []
    resp = await client.get("/api/newcomers/history?days=7&country=JP")
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_record_mock_mode_skips_enrich(client, monkeypatch):
    nl = importlib.import_module("app.services.newcomer_log")
    now = _live("app.database").utcnow_naive()
    today = now.strftime("%Y-%m-%d")
    prev = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    await _seed_rankings(today, prev, "FR", "b_")

    called = []
    async def fake_enrich(*a):
        called.append(a)
    monkeypatch.setattr(nl.settings, "USE_MOCK_DATA", True)
    monkeypatch.setattr(nl, "enrich_fields", fake_enrich)
    monkeypatch.setattr(nl, "_POLITE_DELAY_S", 0)
    r = await nl.record_market_newcomers("FR", "ios")
    assert r["recorded"] == 2 and r["enriched"] == 0 and called == []


@pytest.mark.asyncio
async def test_history_live_attribution(client, monkeypatch):
    """建档发生在检出之后 → 历史端点读时归属：entity_name 出现、is_slg 翻真。"""
    nl = importlib.import_module("app.services.newcomer_log")
    now = _live("app.database").utcnow_naive()
    today = now.strftime("%Y-%m-%d")
    prev = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    await _seed_rankings(today, prev, "GB", "c_")

    monkeypatch.setattr(nl.settings, "USE_MOCK_DATA", True)  # 跳过外呼
    monkeypatch.setattr(nl, "_POLITE_DELAY_S", 0)
    await nl.record_market_newcomers("GB", "ios")

    resp = await client.get("/api/newcomers/history?days=7&country=GB")
    assert all(not i["is_slg"] and i["entity_name"] is None for i in resp.json()["items"])

    # 事后建档：钉住 c_rookie01 的 app_id
    r = await client.post("/api/publishers/", json={
        "name": "新检出厂商甲", "app_ids": [{"app_id": "c_rookie01"}]})
    assert r.status_code == 201

    resp = await client.get("/api/newcomers/history?days=7&country=GB")
    by_id = {i["app_id"]: i for i in resp.json()["items"]}
    hit = by_id["c_rookie01"]
    assert hit["is_slg"] is True and hit["entity_name"] == "新检出厂商甲"
    assert by_id["c_rookie99"]["entity_name"] is None  # 未建档的不受影响


def test_digest_newcomer_enrich_suffix():
    from app.services.release_alerts import build_newcomer_lines
    market = {"newcomers": [{"app_id": "x1", "rank": 7, "name": "寒霜新游",
                             "publisher": "某厂", "revenue": None, "is_slg": True}]}
    lines = build_newcomer_lines(market, {}, enrich={
        "x1": {"genre": "Casual", "price": "Free", "release_date": "2026-06-01"}})
    assert lines == ["✨ **寒霜新游** 空降 **#7** — 某厂（—） · Casual · Free · 上架 2026-06-01"]
    # 无富化数据不占位
    lines2 = build_newcomer_lines(market, {})
    assert "·" not in lines2[0].split("（—）")[1]
