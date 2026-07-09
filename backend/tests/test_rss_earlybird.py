"""RSS 早鸟信号层（ADR 0005）。

核心验证：
- 旧版 RSS JSON 解析（单条 entry 是 dict 不是 list 的经典坑 / 缺 id 跳过 / 榜序）
- 首轮整榜收编基线、不产生早鸟信号
- 次轮 diff：ST 已见 / 检出已见 / 忽略名单三道闸各自拦截；真早鸟落
  chart_type='rss' 影子行 + 返回 items 供 digest 段
- /history 排除 rss 影子行（radar 同款）
- digest：「⚡ RSS 早鸟」段仅维护者卡，领导卡不含
- 中文/韩文夹具（CJK 纪律）
"""
import importlib

import pytest


def _live(mod):
    return importlib.import_module(mod)


def _feed_payload(entries):
    """构造旧版 RSS JSON。entries: [(app_id, name, artist)]。"""
    return {"feed": {"updated": {"label": "2026-07-09T01:00:00-07:00"}, "entry": [
        {"im:name": {"label": name}, "im:artist": {"label": artist},
         "id": {"attributes": {"im:id": aid}}}
        for aid, name, artist in entries
    ]}}


def test_parse_entries_handles_single_dict_and_missing_id():
    from app.services.rss_earlybird import _parse_entries
    # 单条 entry：Apple 返回 dict 而非 list
    single = {"feed": {"entry": {"im:name": {"label": "王国黎明"},
                                 "im:artist": {"label": "某厂"},
                                 "id": {"attributes": {"im:id": "111"}}}}}
    out = _parse_entries(single)
    assert out == [{"app_id": "111", "name": "王国黎明", "publisher": "某厂", "rank": 1}]
    # 缺 id 的条目诚实跳过；rank 按榜序
    payload = _feed_payload([("221", "라스트 퍼리", "스타유니온"), ("222", "第二名", "厂B")])
    payload["feed"]["entry"].insert(1, {"im:name": {"label": "坏条目"}})
    out = _parse_entries(payload)
    assert [(e["app_id"], e["rank"]) for e in out] == [("221", 1), ("222", 3)]
    assert _parse_entries(None) == [] and _parse_entries({"feed": {}}) == []


async def _run_sync(monkeypatch, feed_entries, countries="jp"):
    rb = _live("app.services.rss_earlybird")
    nl = _live("app.services.newcomer_log")
    monkeypatch.setattr(rb.settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(rb.settings, "RSS_EARLYBIRD_COUNTRIES", countries)

    async def fake_fetch(cc, limit):
        return rb._parse_entries(_feed_payload(feed_entries))

    async def fake_enrich(app_id, country, platform):
        return None  # 富化失败留 NULL 不丢检出（同 newcomer_log 哲学）

    monkeypatch.setattr(rb, "_fetch_chart", fake_fetch)
    monkeypatch.setattr(nl, "enrich_fields", fake_enrich)
    monkeypatch.setattr(rb, "_POLITE_DELAY_S", 0)
    return await rb.sync_rss_earlybird()


@pytest.mark.asyncio
async def test_first_run_builds_baseline_without_signals(client, monkeypatch):
    from sqlalchemy import select
    database = _live("app.database")
    RssChartSeen = _live("app.models.newcomer").RssChartSeen
    out = await _run_sync(monkeypatch, [("101", "老牌基建", "厂甲"), ("102", "老牌数字门", "厂乙")])
    assert out["new"] == 0 and out["items"] == []
    assert out["baseline"] == 2
    async with database.AsyncSessionLocal() as db:
        rows = (await db.execute(select(RssChartSeen))).scalars().all()
    assert len(rows) == 2 and all(r.is_baseline for r in rows)


@pytest.mark.asyncio
async def test_second_run_gates_and_detects_earlybird(client, monkeypatch):
    from sqlalchemy import select
    database = _live("app.database")
    m = _live("app.models.newcomer")
    GameRanking = _live("app.models.game").GameRanking
    PublisherIgnore = _live("app.models.publisher").PublisherIgnore

    # 首轮：建基线（1 个老面孔）
    await _run_sync(monkeypatch, [("201", "基线老游戏", "厂甲")])
    # 闸① ST 已见：JP iOS 榜历史里出现过的 app
    async with database.AsyncSessionLocal() as db:
        db.add(GameRanking(app_id="202", date="2026-06-01", rank=50, country="JP",
                           platform="ios", name="ST已见老游戏", publisher="厂乙"))
        # 闸③ 忽略名单（app_id 精确忽略）
        db.add(PublisherIgnore(kind="app_id", value="203", note="人工确认非SLG"))
        await db.commit()

    # 次轮：基线行仍在 + ST 已见 + 被忽略 + 真早鸟（Century Games 命中种子白名单）
    out = await _run_sync(monkeypatch, [
        ("201", "基线老游戏", "厂甲"),
        ("202", "ST已见老游戏", "厂乙"),
        ("203", "麻将噪声", "棋牌厂"),
        ("204", "ホワイトアウト新作", "Century Games Pte. Ltd."),
    ])
    assert out["new"] == 1
    assert out["items"][0]["app_id"] == "204" and out["items"][0]["is_slg"] is True

    async with database.AsyncSessionLocal() as db:
        log_rows = (await db.execute(select(m.MarketNewcomerLog).where(
            m.MarketNewcomerLog.chart_type == "rss"))).scalars().all()
        seen = {r.app_id: r for r in (await db.execute(
            select(m.RssChartSeen))).scalars().all()}
    assert [r.app_id for r in log_rows] == ["204"]
    assert log_rows[0].is_slg is True and log_rows[0].country == "JP"
    assert seen["202"].is_baseline and seen["203"].is_baseline  # 拦截行收编台账不报
    assert seen["204"].is_baseline is False                      # 真早鸟标记
    # 基线行的 last_seen 随次轮更新
    assert seen["201"].last_rank == 1

    # /history 排除 rss 影子行（radar 同款）
    items = (await client.get("/api/newcomers/history?days=7&chart=all")).json()["items"]
    assert all(i["app_id"] != "204" for i in items)

    # 幂等：第三轮同 feed，无新增
    out3 = await _run_sync(monkeypatch, [("204", "ホワイトアウト新作", "Century Games Pte. Ltd.")])
    assert out3["new"] == 0 and out3["items"] == []


def test_digest_rss_section_maintainer_only():
    from app.services.release_alerts import build_daily_digest
    per_combo = [{"country": "US", "platform": "ios", "movement": None,
                  "market": {"as_of": "2026-07-09", "newcomers": [
                      {"app_id": "x1", "rank": 9, "name": "占位新品",
                       "publisher": "Century Games Pte. Ltd.", "is_slg": True}]},
                  "publisher": None, "enrich": None,
                  "free_market": None, "free_publisher": None}]
    rss = [{"country": "JP", "app_id": "204", "name": "ホワイトアウト新作",
            "publisher": "Century Games Pte. Ltd.", "rank": 41, "is_slg": True},
           {"country": "KR", "app_id": "205", "name": "미확인 신작",
            "publisher": "신생 스튜디오", "rank": 77, "is_slg": False}]
    m_card = build_daily_digest(per_combo, "2026-07-09", rss_items=rss)
    l_card = build_daily_digest(per_combo, "2026-07-09", rss_items=rss, audience="leader")
    assert m_card and "⚡ RSS 早鸟" in m_card[1]
    assert "ホワイトアウト新作" in m_card[1] and "⚠️ 待识别" in m_card[1]
    # SLG 优先排序：SLG 行先于待识别行
    assert m_card[1].index("ホワイトアウト新作") < m_card[1].index("미확인 신작")
    assert l_card and "RSS 早鸟" not in l_card[1], "早鸟段不进领导卡"
