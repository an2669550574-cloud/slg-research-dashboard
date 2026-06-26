"""切片 1a：YouTube 实机玩法视频搜索服务（ADR 0002）。纯 mock，不打真网络。

验收锚点（ADR 0002 切片 1a）：mock YT 响应 / 中文游戏名夹具（CJK 硬规则）/
配额超限降级 / key 缺失返回空不抛错。
"""
import pytest

import app.services.youtube_search as ys
from app.config import settings


def _fake_response() -> dict:
    """模拟 YT search.list 响应——含中文标题（CJK 验证）+ 一条无 videoId 的频道结果。"""
    return {
        "items": [
            {"id": {"videoId": "AAA111"},
             "snippet": {"title": "万国觉醒 实机玩法演示 Rise of Kingdoms gameplay",
                         "channelTitle": "出海攻略组",
                         "publishedAt": "2026-06-14T08:00:00Z",
                         "thumbnails": {"medium": {"url": "https://i.ytimg.com/aaa.jpg"}}}},
            {"id": {"videoId": "BBB222"},
             "snippet": {"title": "新区开荒攻略",
                         "channelTitle": "伊原ihara",
                         "publishedAt": "2026-06-23T00:00:00Z",
                         "thumbnails": {"high": {"url": "https://i.ytimg.com/bbb.jpg"}}}},
            # 无 videoId（频道结果）→ 应被过滤掉
            {"id": {"kind": "youtube#channel", "channelId": "C999"},
             "snippet": {"title": "某频道"}},
        ]
    }


@pytest.mark.asyncio
async def test_search_parses_cjk_candidates(monkeypatch):
    """中文游戏名 → query 正确拼接、CJK 标题保真、无 videoId 结果被过滤、rank 递增。"""
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "test-key")
    captured = {}

    async def fake_raw(q, max_results):
        captured["q"], captured["n"] = q, max_results
        return _fake_response()
    monkeypatch.setattr(ys, "_yt_search_raw", fake_raw)

    out = await ys.search_gameplay_videos("万国觉醒", max_results=5)

    assert captured["q"] == "万国觉醒 gameplay"   # 游戏名 + 后缀，中文原样
    assert captured["n"] == 5
    assert [c.video_id for c in out] == ["AAA111", "BBB222"]  # 频道结果被过滤
    first = out[0]
    assert "万国觉醒" in first.title              # CJK 标题保真
    assert first.url == "https://www.youtube.com/watch?v=AAA111"
    assert first.channel == "出海攻略组"
    assert first.thumbnail == "https://i.ytimg.com/aaa.jpg"
    assert first.published_at == "2026-06-14"
    assert first.rank == 1
    assert out[1].rank == 2
    assert out[1].thumbnail == "https://i.ytimg.com/bbb.jpg"  # medium 缺 → 退到 high


@pytest.mark.asyncio
async def test_search_no_key_returns_empty(monkeypatch):
    """key 缺失 → 直接短路返回空，不打 API、不抛错。"""
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", None)
    called = False

    async def fake_raw(q, max_results):
        nonlocal called
        called = True
        return {}
    monkeypatch.setattr(ys, "_yt_search_raw", fake_raw)

    out = await ys.search_gameplay_videos("万国觉醒")
    assert out == []
    assert called is False


@pytest.mark.asyncio
async def test_search_swallows_errors(monkeypatch):
    """请求/解析异常（如 YT 自身 403 配额耗尽）→ 返回空、不断链路。"""
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "test-key")

    async def boom(q, max_results):
        raise RuntimeError("403 quota exceeded")
    monkeypatch.setattr(ys, "_yt_search_raw", boom)

    out = await ys.search_gameplay_videos("末日喧嚣")
    assert out == []


def test_search_gate_dedup_and_quota(monkeypatch):
    """护栏判定纯函数：去重 / 当日配额耗尽 / 正常 三态。"""
    monkeypatch.setattr(settings, "YOUTUBE_SEARCH_DAILY_CAP", 80)

    g = ys.evaluate_search_gate(already_searched=True, used_today=0)
    assert (g.allowed, g.reason) == (False, "duplicate")

    g = ys.evaluate_search_gate(already_searched=False, used_today=80)
    assert (g.allowed, g.reason) == (False, "quota_exhausted")

    g = ys.evaluate_search_gate(already_searched=False, used_today=10)
    assert (g.allowed, g.reason) == (True, "ok")


@pytest.mark.asyncio
async def test_search_dedups_duplicate_video_ids(monkeypatch):
    """YT 单响应内重复 video_id → 去重（防落库撞唯一约束），rank 连续不跳号。"""
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "test-key")

    async def fake_raw(q, max_results):
        return {"items": [
            {"id": {"videoId": "DUP"}, "snippet": {"title": "万国觉醒 实机"}},
            {"id": {"videoId": "DUP"}, "snippet": {"title": "万国觉醒 实机（重复）"}},
            {"id": {"videoId": "X2"}, "snippet": {"title": "另一条"}},
        ]}
    monkeypatch.setattr(ys, "_yt_search_raw", fake_raw)

    out = await ys.search_gameplay_videos("万国觉醒")
    assert [c.video_id for c in out] == ["DUP", "X2"]   # 重复被去掉
    assert [c.rank for c in out] == [1, 2]              # rank 连续
