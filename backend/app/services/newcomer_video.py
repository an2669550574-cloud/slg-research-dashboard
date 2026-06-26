"""新品实机玩法视频持久层（ADR 0002 · 切片 1b）。

从检出日志取「待搜」新品 → 按配额护栏搜 YouTube → 落 newcomer_video 候选 +
记搜索台账。与 newcomer_log（检出沉淀）对称：那是榜单检出的持久层，这是视频的。

「待搜」隐式 = market_newcomer_log 里近 LOOKBACK 天、app_id 不在搜索台账
（newcomer_video_search）的行。台账记已搜 app（去重）+ searched_at（当日配额计数）。
当日触达 DAILY_CAP 即停，剩余下次 drain 自然仍在待搜集——无显式队列状态机。
"""
import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.newcomer import MarketNewcomerLog, NewcomerVideo, NewcomerVideoSearch
from app.services.youtube_search import evaluate_search_gate, search_gameplay_videos

logger = logging.getLogger(__name__)


async def sync_newcomer_videos(daily_cap: int | None = None,
                               lookback_days: int | None = None) -> dict:
    """跑一轮视频搜集，返回 {searched, videos, pending_left}。

    - YOUTUBE_API_KEY 未配 → 整体 no-op（返回零），与 enrich 同哲学。
    - 护栏：同 app 不重搜（台账去重）、当日上限 DAILY_CAP（UTC 日，搜过即计数、
      含 0 结果）、超额留待下次（pending_left 计数，不静默丢）。
    - 单次搜失败（search_gameplay_videos 返回空）仍记台账 done——避免坏名字
      每天重试烧配额；要重搜删台账行即可。
    """
    out = {"searched": 0, "videos": 0, "pending_left": 0}
    if not settings.YOUTUBE_API_KEY:
        return out
    cap = settings.YOUTUBE_SEARCH_DAILY_CAP if daily_cap is None else daily_cap
    lookback = (settings.YOUTUBE_SEARCH_LOOKBACK_DAYS
                if lookback_days is None else lookback_days)

    async with AsyncSessionLocal() as db:
        now = utcnow_naive()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        used_today = await db.scalar(
            select(func.count()).select_from(NewcomerVideoSearch).where(
                NewcomerVideoSearch.searched_at >= day_start)) or 0
        already = set((await db.execute(
            select(NewcomerVideoSearch.app_id))).scalars().all())

        # 待搜：近 lookback 天检出、未搜过的 app（去重、新到旧）。lookback<=0 = 不限。
        q = select(MarketNewcomerLog.app_id, MarketNewcomerLog.name).order_by(
            MarketNewcomerLog.first_detected_at.desc())
        if lookback and lookback > 0:
            q = q.where(MarketNewcomerLog.first_detected_at >= now - timedelta(days=lookback))
        rows = (await db.execute(q)).all()

        seen: set[str] = set()
        for app_id, name in rows:
            if app_id in already or app_id in seen:
                continue
            seen.add(app_id)
            gate = evaluate_search_gate(already_searched=False,
                                        used_today=used_today, daily_cap=cap)
            if not gate.allowed:  # quota_exhausted → 留待下次 drain
                out["pending_left"] += 1
                continue
            vids = await search_gameplay_videos(name)
            try:
                # savepoint 隔离单 app：万一撞唯一约束（残余重复 video_id /
                # 并发两轮 drain 撞 app_id unique），只回滚这一个 app，不毁整轮。
                async with db.begin_nested():
                    for v in vids:
                        db.add(NewcomerVideo(
                            app_id=app_id, video_id=v.video_id, title=v.title,
                            channel=v.channel, thumbnail=v.thumbnail, url=v.url,
                            published_at=v.published_at, rank=v.rank))
                    db.add(NewcomerVideoSearch(app_id=app_id, name=name,
                                               result_count=len(vids), searched_at=now))
            except IntegrityError:
                logger.warning("video sync: skip app %s (integrity, likely concurrent/dup)", app_id)
                continue
            used_today += 1
            out["searched"] += 1
            out["videos"] += len(vids)
        await db.commit()

    if out["searched"] or out["pending_left"]:
        logger.info("newcomer video sync: %s", out)
    return out
