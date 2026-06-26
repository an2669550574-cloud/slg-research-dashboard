"""切片 1b：新品实机玩法视频持久层（ADR 0002）。

验收锚点：检出后落库有候选 / 同 app 不重复搜 / 当日上限超额排次日 / key 未配
no-op / lookback 排除老检出。用 app fixture 建表，直接读写 db、monkeypatch 搜索。
"""
from datetime import timedelta

import pytest
from sqlalchemy import select


def _candidates(name: str):
    """假 YT 候选（中文标题验 CJK）。"""
    from app.services.youtube_search import VideoCandidate
    return [
        VideoCandidate(video_id="V1", title=f"{name} 实机玩法演示",
                       url="https://www.youtube.com/watch?v=V1", channel="出海攻略组",
                       thumbnail="https://i.ytimg.com/v1.jpg", published_at="2026-06-14", rank=1),
        VideoCandidate(video_id="V2", title=f"{name} gameplay walkthrough",
                       url="https://www.youtube.com/watch?v=V2", channel="ihara",
                       thumbnail="https://i.ytimg.com/v2.jpg", published_at="2026-06-10", rank=2),
    ]


async def _add_log(app_id: str, name: str, detected=None):
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.newcomer import MarketNewcomerLog
    async with AsyncSessionLocal() as db:
        row = MarketNewcomerLog(country="US", platform="ios", app_id=app_id,
                                as_of="2026-06-26", name=name)
        row.first_detected_at = detected or utcnow_naive()
        db.add(row)
        await db.commit()


@pytest.mark.asyncio
async def test_sync_searches_and_stores_cjk(app, monkeypatch):
    """检出新品 → 搜 → 落候选 + 记台账；中文名/标题保真。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.newcomer import NewcomerVideo, NewcomerVideoSearch
    from app.services import newcomer_video as nv
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "test-key")

    async def fake_search(name, max_results=None):
        return _candidates(name)
    monkeypatch.setattr(nv, "search_gameplay_videos", fake_search)

    await _add_log("111", "万国觉醒")
    await _add_log("222", "末日喧嚣")

    out = await nv.sync_newcomer_videos()
    assert out == {"searched": 2, "videos": 4, "pending_left": 0}

    async with AsyncSessionLocal() as db:
        vids = (await db.execute(select(NewcomerVideo))).scalars().all()
        tasks = (await db.execute(select(NewcomerVideoSearch))).scalars().all()
    assert len(vids) == 4
    assert {t.app_id for t in tasks} == {"111", "222"}
    assert all(t.result_count == 2 for t in tasks)
    assert any("万国觉醒" in v.title for v in vids)            # CJK 标题保真
    assert any(v.app_id == "111" and v.rank == 1 for v in vids)


@pytest.mark.asyncio
async def test_sync_dedup_skips_already_searched(app, monkeypatch):
    """同 app 不重复搜：第二轮 drain 对已搜 app 直接跳过。"""
    from app.config import settings
    from app.services import newcomer_video as nv
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "test-key")
    calls = []

    async def fake_search(name, max_results=None):
        calls.append(name)
        return _candidates(name)
    monkeypatch.setattr(nv, "search_gameplay_videos", fake_search)

    await _add_log("111", "万国觉醒")
    first = await nv.sync_newcomer_videos()
    second = await nv.sync_newcomer_videos()

    assert first["searched"] == 1
    assert second["searched"] == 0          # 已在台账，不再搜
    assert calls == ["万国觉醒"]            # 只调一次 YT


@pytest.mark.asyncio
async def test_sync_daily_cap_defers_overflow(app, monkeypatch):
    """当日上限：超额的 app 不搜、计入 pending_left，留待下次（不静默丢）。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.newcomer import NewcomerVideoSearch
    from app.services import newcomer_video as nv
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "test-key")

    async def fake_search(name, max_results=None):
        return _candidates(name)
    monkeypatch.setattr(nv, "search_gameplay_videos", fake_search)

    for i in range(5):
        await _add_log(f"app{i}", f"游戏{i}")

    out = await nv.sync_newcomer_videos(daily_cap=2)
    assert out["searched"] == 2
    assert out["pending_left"] == 3
    async with AsyncSessionLocal() as db:
        n = len((await db.execute(select(NewcomerVideoSearch))).scalars().all())
    assert n == 2


@pytest.mark.asyncio
async def test_sync_no_key_is_noop(app, monkeypatch):
    """YOUTUBE_API_KEY 未配 → 整体 no-op，不搜不落库。"""
    from app.config import settings
    from app.services import newcomer_video as nv
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", None)
    called = False

    async def fake_search(name, max_results=None):
        nonlocal called
        called = True
        return _candidates(name)
    monkeypatch.setattr(nv, "search_gameplay_videos", fake_search)

    await _add_log("111", "万国觉醒")
    out = await nv.sync_newcomer_videos()
    assert out == {"searched": 0, "videos": 0, "pending_left": 0}
    assert called is False


@pytest.mark.asyncio
async def test_sync_lookback_excludes_old_detections(app, monkeypatch):
    """lookback 只搜近 N 天检出；更老的检出不搜（防首搜把历史全量搜爆）。"""
    from app.config import settings
    from app.database import utcnow_naive
    from app.services import newcomer_video as nv
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "test-key")
    searched_names = []

    async def fake_search(name, max_results=None):
        searched_names.append(name)
        return _candidates(name)
    monkeypatch.setattr(nv, "search_gameplay_videos", fake_search)

    await _add_log("new1", "新检出", detected=utcnow_naive())
    await _add_log("old1", "老检出", detected=utcnow_naive() - timedelta(days=100))

    out = await nv.sync_newcomer_videos(lookback_days=30)
    assert out["searched"] == 1
    assert searched_names == ["新检出"]


@pytest.mark.asyncio
async def test_videos_endpoint_lists_and_deletes(client):
    """读端点按候选序返回；删端点去噪一条（CJK 标题）。"""
    from app.database import AsyncSessionLocal
    from app.models.newcomer import NewcomerVideo
    async with AsyncSessionLocal() as db:
        db.add(NewcomerVideo(app_id="111", video_id="V1", title="万国觉醒 实机玩法",
                             url="https://www.youtube.com/watch?v=V1", rank=1))
        db.add(NewcomerVideo(app_id="111", video_id="V2", title="gameplay",
                             url="https://www.youtube.com/watch?v=V2", rank=2))
        await db.commit()

    r = await client.get("/api/newcomers/videos", params={"app_id": "111"})
    assert r.status_code == 200
    body = r.json()
    assert [v["video_id"] for v in body] == ["V1", "V2"]
    assert "万国觉醒" in body[0]["title"]

    rd = await client.delete(f"/api/newcomers/videos/{body[0]['id']}")
    assert rd.status_code == 200
    r2 = await client.get("/api/newcomers/videos", params={"app_id": "111"})
    assert [v["video_id"] for v in r2.json()] == ["V2"]


@pytest.mark.asyncio
async def test_sync_survives_integrity_error_per_app(app, monkeypatch):
    """单 app 撞唯一约束（残余重复 video_id）→ savepoint 只回滚该 app，整轮不毁、好 app 照常落库。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.newcomer import NewcomerVideo, NewcomerVideoSearch
    from app.services import newcomer_video as nv
    from app.services.youtube_search import VideoCandidate
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "test-key")

    async def fake_search(name, max_results=None):
        if name == "坏游戏":  # 模拟去重失效：同 video_id 两条 → savepoint 内撞 uq_newcomer_video
            return [VideoCandidate("DUP", "t1", "https://y/DUP", None, None, None, 1),
                    VideoCandidate("DUP", "t2", "https://y/DUP", None, None, None, 2)]
        return [VideoCandidate("OK1", "好视频", "https://y/OK1", None, None, None, 1)]
    monkeypatch.setattr(nv, "search_gameplay_videos", fake_search)

    await _add_log("bad", "坏游戏")
    await _add_log("good", "好游戏")
    out = await nv.sync_newcomer_videos()

    assert out["videos"] == 1          # 只有 good 落库，整轮没回滚
    async with AsyncSessionLocal() as db:
        apps = {v.app_id for v in (await db.execute(select(NewcomerVideo))).scalars().all()}
        tasks = {t.app_id for t in (await db.execute(select(NewcomerVideoSearch))).scalars().all()}
    assert apps == {"good"}
    assert "good" in tasks
    assert "bad" not in tasks          # 坏 app 没进台账，下次可重试
