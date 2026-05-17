"""历史回填：_parse_sales_series 解析、fetch_sales_series 配额纪律、
build_history 让 rank=NULL 的回填行也能进收入峰值。

conftest 每个 test 重载 app.* —— app.* import 放函数内。
"""
import pytest
from unittest.mock import patch, AsyncMock


def test_parse_sales_series_ios_groups_by_app_and_date():
    from app.services.sensor_tower import _parse_sales_series
    data = [
        {"aid": "111", "d": "2026-05-01T00:00:00Z", "iu": 10, "au": 5, "ir": 100, "ar": 0},
        {"aid": "111", "d": "2026-05-02T00:00:00Z", "iu": 20, "au": 0, "ir": 250, "ar": 0},
        # 同 app 同日多区 → 累加
        {"aid": "111", "d": "2026-05-02T00:00:00Z", "iu": 0, "au": 3, "ir": 50, "ar": 0},
        {"aid": "222", "d": "2026-05-01T00:00:00Z", "iu": 7, "au": 0, "ir": 99, "ar": 0},
    ]
    out = _parse_sales_series(data, "ios")
    assert out["111"]["2026-05-01"] == {"downloads": 15, "revenue": 1.0}
    assert out["111"]["2026-05-02"] == {"downloads": 23, "revenue": 3.0}  # 250+50 分
    assert out["222"]["2026-05-01"] == {"downloads": 7, "revenue": 0.99}


def test_parse_sales_series_android_fields():
    from app.services.sensor_tower import _parse_sales_series
    data = [{"app_id": "com.x", "date": "2026-04-30T00:00:00Z", "u": 12, "r": 4500}]
    out = _parse_sales_series(data, "android")
    assert out["com.x"]["2026-04-30"] == {"downloads": 12, "revenue": 45.0}


@pytest.mark.asyncio
async def test_fetch_sales_series_quota_discipline(client, monkeypatch):
    from app.services import sensor_tower as st
    svc = st.sensor_tower_service
    monkeypatch.setattr(svc, "use_mock", False)

    # 配额耗尽 → None（调用方应停）
    with patch.object(st.quota, "try_consume", new=AsyncMock(return_value=False)):
        assert await svc.fetch_sales_series(["1"], "US", "ios", "2026-01-01", "2026-01-02") is None

    # _get 抛错 → 退还配额 + 返回 {}（调用方可继续）
    refund = AsyncMock()
    with patch.object(st.quota, "try_consume", new=AsyncMock(return_value=True)), \
         patch.object(st.quota, "refund", new=refund), \
         patch.object(svc, "_get", new=AsyncMock(side_effect=RuntimeError("boom"))):
        assert await svc.fetch_sales_series(["1"], "US", "ios", "2026-01-01", "2026-01-02") == {}
    refund.assert_awaited_once()

    # 正常 → 解析后的序列
    payload = [{"aid": "1", "d": "2026-01-01T00:00:00Z", "iu": 9, "ir": 100}]
    with patch.object(st.quota, "try_consume", new=AsyncMock(return_value=True)), \
         patch.object(svc, "_get", new=AsyncMock(return_value=payload)):
        out = await svc.fetch_sales_series(["1"], "US", "ios", "2026-01-01", "2026-01-02")
    assert out["1"]["2026-01-01"] == {"downloads": 9, "revenue": 1.0}


@pytest.mark.asyncio
async def test_build_history_uses_rank_null_backfill_rows(client):
    """纯历史回填行（rank=NULL，有 revenue）也应产出收入峰值事件，
    且描述里不出现 '#None'；不产出任何 ranking 类事件。"""
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking
    from app.services.history_builder import build_history

    async with AsyncSessionLocal() as db:
        for d, rev in [("2026-03-01", 80000.0), ("2026-03-02", 300000.0)]:
            db.add(GameRanking(app_id="backfill.only", date=d, rank=None,
                               downloads=1234, revenue=rev, country="US",
                               platform="ios", name=None, publisher=None, icon_url=None))
        await db.commit()
        with patch("app.services.history_builder.fetch_app_info",
                   new=AsyncMock(return_value=None)):
            events = await build_history("backfill.only", db)

    assert [e["event_type"] for e in events] == ["revenue"]
    ev = events[0]
    assert "$300,000" in ev["title"]
    assert "#None" not in ev["description"]
    assert "下载 1,234" in ev["description"]
