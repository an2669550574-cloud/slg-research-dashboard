"""新品实机玩法视频自动搜集：YouTube Data API 搜索服务（ADR 0002 · 切片 1a）。

竞品新品检出后，按「游戏名 + gameplay」搜 YouTube 拿实机玩法视频候选。
- YT 独立配额（10000 units/天，search.list = 100 units/次），完全不碰 Sensor Tower 池。
- YOUTUBE_API_KEY 留空 → 整体 no-op（search 返回空、不抛错），与 newcomer enrich 同哲学。
- 本模块只负责「搜一次 + 解析 + 护栏判定」，纯逻辑无持久层；落库 / 去重锚点 /
  当日计数 / 待搜队列 / 触发挂载在切片 1b（newcomer_video 表）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

YT_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_WATCH_URL = "https://www.youtube.com/watch?v="


@dataclass
class VideoCandidate:
    """一条 YouTube 实机视频候选（切片 1b 落 newcomer_video 表的字段同形）。"""
    video_id: str
    title: str
    url: str
    channel: Optional[str]
    thumbnail: Optional[str]
    published_at: Optional[str]  # YYYY-MM-DD
    rank: int                    # 候选序，1 起（= YT 相关性排序位次）


@dataclass
class SearchGate:
    """配额护栏判定结果（纯函数产物，切片 1b 据此决定搜 / 跳过 / 排次日）。"""
    allowed: bool
    reason: str  # "ok" | "duplicate" | "quota_exhausted"


def evaluate_search_gate(already_searched: bool, used_today: int,
                         daily_cap: Optional[int] = None) -> SearchGate:
    """决定某 app_id 此刻是否该搜。纯函数、无 IO，便于单测与跨副本一致。

    - already_searched=True → duplicate（同 app_id 不重复搜，省配额）。
    - used_today ≥ daily_cap → quota_exhausted（调用方排次日，不静默丢）。
    - 否则 ok。
    """
    cap = settings.YOUTUBE_SEARCH_DAILY_CAP if daily_cap is None else daily_cap
    if already_searched:
        return SearchGate(False, "duplicate")
    if used_today >= cap:
        return SearchGate(False, "quota_exhausted")
    return SearchGate(True, "ok")


def _parse_item(item: dict, rank: int) -> Optional[VideoCandidate]:
    """YT search item → VideoCandidate；无 videoId（频道/播放列表结果）跳过。"""
    vid = (item.get("id") or {}).get("videoId")
    if not vid:
        return None
    sn = item.get("snippet") or {}
    thumbs = sn.get("thumbnails") or {}
    thumb = ((thumbs.get("medium") or thumbs.get("high") or thumbs.get("default") or {})
             .get("url"))
    return VideoCandidate(
        video_id=vid,
        title=(sn.get("title") or "").strip(),
        url=_WATCH_URL + vid,
        channel=(sn.get("channelTitle") or "").strip() or None,
        thumbnail=thumb,
        published_at=(sn.get("publishedAt") or "")[:10] or None,
        rank=rank,
    )


async def _yt_search_raw(q: str, max_results: int) -> dict:
    """打一次 YT search.list 拿原始 JSON。抽成独立函数便于测试 monkeypatch。"""
    params = {
        "key": settings.YOUTUBE_API_KEY,
        "part": "snippet",
        "type": "video",
        "q": q,
        "maxResults": max_results,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(YT_SEARCH_URL, params=params)
        resp.raise_for_status()
        return resp.json()


async def search_gameplay_videos(name: str,
                                 max_results: Optional[int] = None
                                 ) -> list[VideoCandidate]:
    """按「游戏名 + 后缀」搜 YouTube 实机玩法视频候选。

    key 缺失 / 空名 / 任何请求 / 解析失败都返回 []（不抛错、不断链路），与
    newcomer enrich 的 found=False 同哲学。一次调用消耗 1 次 search.list（100 units）。
    """
    name = (name or "").strip()
    if not settings.YOUTUBE_API_KEY or not name:
        return []
    n = max_results or settings.YOUTUBE_SEARCH_MAX_RESULTS
    suffix = (settings.YOUTUBE_SEARCH_QUERY_SUFFIX or "").strip()
    # 游戏名加引号精确匹配：防 YT 对通用/短名拆词召回他游噪声。prod 实测：
    # '탑 로드'（Top Lords）裸搜全是 Million Lords/Bannerlord/赛马娘等拆词噪声，
    # 加引号后大半命中真实机；对独特名（Infinity Kingdom）无害。name 内的引号
    # 先换成空格，避免破坏 q 语法。不加 videoDuration 过滤——实测 medium 会把
    # 实机（常是 short 片段 / long 完整实况）滤掉，只留中等时长的解说/tips。
    safe = name.replace('"', " ").strip()
    q = f'"{safe}" {suffix}'.strip()
    try:
        data = await _yt_search_raw(q, n)
    except Exception:
        logger.warning("youtube search failed for %r", q, exc_info=True)
        return []
    # 对 video_id 去重：YT 单次响应偶发同一视频重复出现，若原样下传，落库层
    # (app_id, video_id) 唯一约束会在 commit 抛 IntegrityError。rank 用去重后的
    # 连续序（len(out)+1），保持 1..N 不跳号。
    out: list[VideoCandidate] = []
    seen_ids: set[str] = set()
    for item in data.get("items") or []:
        cand = _parse_item(item, len(out) + 1)
        if cand is None or cand.video_id in seen_ids:
            continue
        seen_ids.add(cand.video_id)
        out.append(cand)
    return out
