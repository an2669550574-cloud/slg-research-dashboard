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


async def _add_log(app_id: str, name: str, detected=None,
                   publisher="FunPlus", subgenre_cn=None, country="US"):
    """默认 publisher=FunPlus（种子 SLG 马甲）→ 过 SLG 门控，让机制测试聚焦搜集逻辑。
    验门控本身的用例显式传非 SLG publisher / SLG subgenre_cn。country 可变以建同 app
    的跨 combo 多行（唯一键=country+platform+app_id+chart_type）。"""
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.newcomer import MarketNewcomerLog
    async with AsyncSessionLocal() as db:
        row = MarketNewcomerLog(country=country, platform="ios", app_id=app_id,
                                as_of="2026-06-26", name=name, publisher=publisher,
                                subgenre_cn=subgenre_cn)
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
async def test_sync_gates_out_non_slg_newcomers(app, monkeypatch):
    """SLG 门控（ADR 0002 范围=竞品 SLG）：只搜 SLG 新品，非 SLG（足球/扑克/纸牌）
    不搜、不进台账（留待后续再评估）。夹具用 prod 真实最糟样本。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.newcomer import NewcomerVideoSearch
    from app.services import newcomer_video as nv
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "test-key")
    searched = []

    async def fake_search(name, max_results=None):
        searched.append(name)
        return _candidates(name)
    monkeypatch.setattr(nv, "search_gameplay_videos", fake_search)

    # 真实最糟样本：同名异物/泛域噪声，publisher 均非追踪 SLG 厂商、无 SLG 题材。
    await _add_log("1193933380", "Head Ball 2 - Игра в футбол",
                   publisher="Masomo", subgenre_cn="其他")            # 足球
    await _add_log("6766608185", "Konkani Kurdi - کۆنکانی کوردی",
                   publisher="Kurdi Games", subgenre_cn=None)          # 库尔德 okey 纸牌
    await _add_log("1605547429", "Falcon Poker&Texas Hold'em",
                   publisher="Falcon", subgenre_cn="其他")             # 扑克
    # 真 SLG 竞品（种子马甲 FunPlus）——应被搜。
    await _add_log("111", "State of Survival", publisher="FunPlus")

    out = await nv.sync_newcomer_videos()
    assert out["searched"] == 1
    assert searched == ["State of Survival"]                          # 只搜 SLG
    async with AsyncSessionLocal() as db:
        marked = {t.app_id for t in
                  (await db.execute(select(NewcomerVideoSearch))).scalars().all()}
    assert marked == {"111"}                                          # 非 SLG 未进台账


@pytest.mark.asyncio
async def test_sync_rescues_slg_by_subgenre(app, monkeypatch):
    """subgenre_cn 题材含 'SLG' 救「非追踪厂商但确是 SLG 游戏」的真竞品
    （Stronghold Kingdoms 国战SLG / My Lands 基地建设SLG，is_slg=0）；
    同为 is_slg=0 但题材非 SLG（塔防）仍被砍。"""
    from app.config import settings
    from app.services import newcomer_video as nv
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "test-key")
    searched = []

    async def fake_search(name, max_results=None):
        searched.append(name)
        return _candidates(name)
    monkeypatch.setattr(nv, "search_gameplay_videos", fake_search)

    await _add_log("1201717505", "Stronghold Kingdoms: Замки",
                   publisher="Firefly Studios", subgenre_cn="国战SLG")
    await _add_log("816221266", "My Lands",
                   publisher="Strategy First", subgenre_cn="基地建设SLG")
    await _add_log("6737409896", "Hellsquad Rrrush!",
                   publisher="Habby", subgenre_cn="塔防")               # 非 SLG 题材 → 砍

    out = await nv.sync_newcomer_videos()
    assert out["searched"] == 2
    assert set(searched) == {"Stronghold Kingdoms: Замки", "My Lands"}


@pytest.mark.asyncio
async def test_sync_subgenre_rescue_aggregates_across_rows(app, monkeypatch):
    """subgenre_cn 稀疏、可能只标在该 app 的某一行：门控按 app_id 聚合判定，
    不因 dedup 取到 subgenre 为空的那行就误砍真 SLG。"""
    from app.config import settings
    from app.database import utcnow_naive
    from app.services import newcomer_video as nv
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "test-key")
    searched = []

    async def fake_search(name, max_results=None):
        searched.append(name)
        return _candidates(name)
    monkeypatch.setattr(nv, "search_gameplay_videos", fake_search)

    # 同 app 两行（跨 combo）：新行 subgenre 空、老行标 国战SLG。dedup 取新行，
    # 但门控应据「任一行题材含 SLG」保留。
    now = utcnow_naive()
    await _add_log("6686394372", "Age of History 3", publisher="Łukasz Jakowski",
                   subgenre_cn=None, detected=now, country="US")
    await _add_log("6686394372", "Age of History 3", publisher="Łukasz Jakowski",
                   subgenre_cn="国战SLG", detected=now - timedelta(days=1), country="JP")

    out = await nv.sync_newcomer_videos()
    assert out["searched"] == 1
    assert searched == ["Age of History 3"]


@pytest.mark.asyncio
async def test_videos_endpoint_lists_and_soft_deletes(client):
    """读端点按候选序返回；删端点软删去噪一条（CJK 标题）——行保留、默认列表隐藏、
    include_hidden 可取回，且噪声样本带 hidden_at（供回溯统计召回质量）。"""
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
    assert body[0]["hidden_at"] is None

    rd = await client.delete(f"/api/newcomers/videos/{body[0]['id']}")
    assert rd.status_code == 200

    # 默认列表：被删的 V1 隐藏，只剩 V2。
    r2 = await client.get("/api/newcomers/videos", params={"app_id": "111"})
    assert [v["video_id"] for v in r2.json()] == ["V2"]

    # include_hidden：V1 仍在（行未物删），带 hidden_at 戳记 → 噪声样本可回溯。
    r3 = await client.get("/api/newcomers/videos",
                          params={"app_id": "111", "include_hidden": "true"})
    all_rows = {v["video_id"]: v for v in r3.json()}
    assert set(all_rows) == {"V1", "V2"}
    assert all_rows["V1"]["hidden_at"] is not None
    assert all_rows["V2"]["hidden_at"] is None


@pytest.mark.asyncio
async def test_delete_video_idempotent(client):
    """重复删同一条：幂等、不刷新 hidden_at（首删时刻为准）。"""
    from app.database import AsyncSessionLocal
    from app.models.newcomer import NewcomerVideo
    async with AsyncSessionLocal() as db:
        db.add(NewcomerVideo(app_id="222", video_id="W1", title="噪声候选",
                             url="https://www.youtube.com/watch?v=W1", rank=1))
        await db.commit()

    body = (await client.get("/api/newcomers/videos", params={"app_id": "222"})).json()
    vid = body[0]["id"]
    await client.delete(f"/api/newcomers/videos/{vid}")
    first = (await client.get("/api/newcomers/videos",
             params={"app_id": "222", "include_hidden": "true"})).json()[0]["hidden_at"]
    await client.delete(f"/api/newcomers/videos/{vid}")  # 二次删
    second = (await client.get("/api/newcomers/videos",
              params={"app_id": "222", "include_hidden": "true"})).json()[0]["hidden_at"]
    assert first is not None and first == second  # 时刻不被二次删刷新


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
