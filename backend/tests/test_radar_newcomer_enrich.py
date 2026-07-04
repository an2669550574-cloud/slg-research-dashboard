"""商店雷达软启动新品接入富化管道（P1-1）。

验收：
- ingest_artist_apps 对 SLG 主体的真新上架收集 radar_newcomers（首同步基线不收；非 SLG
  主体不收；toggle 关时不收）
- record_radar_newcomers 写 market_newcomer_log chart_type='radar' 影子行（富化字段落库、
  summary_cn 留 NULL、rank NULL、is_slg=True），幂等
- 影子行是 translate 候选（有描述、summary_cn 缺）→ riding 中文化管道
- /history 排除影子行（不进市场卡片网格，含 chart=all）
- 中文夹具（CJK 纪律）
"""
import pytest
from sqlalchemy import select


async def _mk_entity_with_artist(client, name="江娱互动测试", artist_id="1717022676",
                                 label="River Game HK", is_slg=True):
    r = await client.post("/api/publishers/", json={"name": name, "is_slg": is_slg})
    assert r.status_code == 201
    entity = r.json()
    r2 = await client.post(f"/api/publishers/{entity['id']}/itunes-artists",
                           json={"artist_id": artist_id, "label": label})
    assert r2.status_code == 201
    return entity, r2.json()


def _app(track_id, name, release_date="2026-06-28", storefronts=None):
    r = {
        "wrapperType": "software", "trackId": track_id, "trackName": name,
        "bundleId": f"com.test.{track_id}",
        "releaseDate": f"{release_date}T00:00:00Z",
        "trackViewUrl": f"https://apps.apple.com/us/app/id{track_id}",
        "artworkUrl512": f"https://mzstatic.test/{track_id}.jpg",
        "genres": ["Games", "Strategy"], "primaryGenreName": "Games",
        "averageUserRating": 4.6, "userRatingCount": 88, "formattedPrice": "Free",
        "description": f"《{name}》——末日避难所建设策略新游，软启动测试区首发。",
        "screenshotUrls": [], "languageCodesISO2A": ["EN", "ZH"],
    }
    if storefronts is not None:
        r["_seen_storefronts"] = set(storefronts)
    return r


async def _radar_rows():
    from app.database import AsyncSessionLocal
    from app.models.newcomer import MarketNewcomerLog
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(MarketNewcomerLog).where(
            MarketNewcomerLog.chart_type == "radar"))).scalars().all()
    return rows


@pytest.mark.asyncio
async def test_ingest_collects_radar_for_slg_new_release(client):
    """SLG 主体真新上架 → radar_newcomers 收集；首同步基线不收。"""
    from app.services.itunes_releases import ingest_artist_apps
    _e, artist = await _mk_entity_with_artist(client)
    r1 = await ingest_artist_apps(artist["id"], [_app(200, "口袋奇兵")])
    assert r1["radar_newcomers"] == []                       # 首同步全基线，不收
    r2 = await ingest_artist_apps(artist["id"], [
        _app(200, "口袋奇兵"), _app(201, "末日先锋：新区", storefronts={"ph"})])
    assert r2["new_apps"] == 1
    rc = r2["radar_newcomers"]
    assert len(rc) == 1
    assert rc[0]["track_id"] == "201" and rc[0]["platform"] == "ios"
    assert rc[0]["country"] == "PH"                          # 软启动可见区
    assert "末日避难所" in (rc[0]["description"] or "")


@pytest.mark.asyncio
async def test_ingest_skips_non_slg_entity(client):
    """非 SLG 主体（未钉该 app）→ 真新上架不收（不把非 SLG 塞进富化管道）。"""
    from app.services.itunes_releases import ingest_artist_apps
    _e, artist = await _mk_entity_with_artist(client, name="多品类大厂测试",
                                              artist_id="9999", is_slg=False)
    await ingest_artist_apps(artist["id"], [_app(300, "某休闲游戏")])
    r2 = await ingest_artist_apps(artist["id"], [
        _app(300, "某休闲游戏"), _app(301, "又一款非SLG", storefronts={"us"})])
    assert r2["new_apps"] == 1 and r2["radar_newcomers"] == []


@pytest.mark.asyncio
async def test_ingest_respects_toggle(client, monkeypatch):
    """RADAR_NEWCOMER_ENRICH_ENABLED=False → 不收集。"""
    from app.config import settings
    from app.services.itunes_releases import ingest_artist_apps
    monkeypatch.setattr(settings, "RADAR_NEWCOMER_ENRICH_ENABLED", False)
    _e, artist = await _mk_entity_with_artist(client)
    await ingest_artist_apps(artist["id"], [_app(400, "基线游戏")])
    r2 = await ingest_artist_apps(artist["id"], [
        _app(400, "基线游戏"), _app(401, "新游", storefronts={"ph"})])
    assert r2["new_apps"] == 1 and r2["radar_newcomers"] == []


@pytest.mark.asyncio
async def test_record_radar_shadow_rows_and_idempotent(client):
    """record_radar_newcomers 写 chart_type='radar' 影子行；富化字段落库、summary_cn 留 NULL；幂等。"""
    from app.services.newcomer_log import record_radar_newcomers
    rows_in = [{
        "track_id": "500", "name": "深海堡垒", "publisher": "测试主体",
        "country": "PH", "platform": "ios", "artwork_url": "https://mzstatic.test/500.jpg",
        "track_view_url": "https://apps.apple.com/id500", "release_date": "2026-06-28",
        "genre": "Strategy", "rating": 4.5, "rating_count": 12, "price": "Free",
        "description": "深海末世建设 SLG。", "screenshot_urls": None, "languages": "EN,ZH",
    }]
    assert await record_radar_newcomers(rows_in) == 1
    rows = await _radar_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.app_id == "500" and row.chart_type == "radar"
    assert row.rank is None and row.is_slg is True
    assert row.description == "深海末世建设 SLG。" and row.summary_cn is None  # 待 LLM drain
    assert row.icon_url == "https://mzstatic.test/500.jpg" and row.enrich_source == "itunes"
    # 幂等：同 (country,platform,app_id,'radar') 不重复
    assert await record_radar_newcomers(rows_in) == 0
    assert len(await _radar_rows()) == 1


@pytest.mark.asyncio
async def test_shadow_row_is_translate_candidate(client):
    """影子行有描述、summary_cn 缺 → 被 translate_pending_newcomers 选中（riding 中文化）。"""
    from app.services.newcomer_log import record_radar_newcomers
    from app.config import settings
    from app.services import newcomer_i18n as ni
    await record_radar_newcomers([{
        "track_id": "600", "name": "末日方舟", "publisher": "测试", "country": "SG",
        "platform": "ios", "description": "末日生存基地建设 SLG。", "genre": "Strategy",
    }])
    # translate 用假 client：影子行应被选中并写回 summary_cn
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "TAISHI_API_KEY", "k")

    class _C:
        class chat:
            class completions:
                @staticmethod
                async def create(**kw):
                    class R:
                        choices = [type("X", (), {"message": type("M", (), {
                            "content": '{"summary":"末日生存 SLG","subgenre":"基地建设SLG","translation":"中文。"}'})()})()]
                        usage = {"prompt_tokens": 10, "completion_tokens": 5}
                    return R()
    monkeypatch.setattr(ni.llm_gateway, "get_client", lambda: _C())
    done = await ni.translate_pending_newcomers()
    monkeypatch.undo()
    assert done == 1
    rows = await _radar_rows()
    assert rows[0].summary_cn == "末日生存 SLG" and rows[0].subgenre_cn == "基地建设SLG"


@pytest.mark.asyncio
async def test_history_excludes_radar_shadow_rows(client):
    """/history 排除 chart_type='radar' 影子行（不进市场卡片网格，含 chart=all）。"""
    from app.services.newcomer_log import record_radar_newcomers
    from app.database import AsyncSessionLocal
    from app.models.newcomer import MarketNewcomerLog
    # 一条真收入榜检出 + 一条雷达影子行
    async with AsyncSessionLocal() as db:
        db.add(MarketNewcomerLog(country="US", platform="ios", app_id="700", as_of="2026-06-28",
                                 name="真榜新品", chart_type="grossing", is_slg=True, rank=5))
        await db.commit()
    await record_radar_newcomers([{"track_id": "701", "name": "雷达影子", "country": "PH",
                                   "platform": "ios", "description": "x"}])
    for chart in ("grossing", "all"):
        resp = await client.get(f"/api/newcomers/history?chart={chart}")
        names = [i["name"] for i in resp.json()["items"]]
        assert "真榜新品" in names
        assert "雷达影子" not in names        # 影子行绝不进市场网格
