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
from app.services.slg_publishers import is_slg
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
        q = select(MarketNewcomerLog.app_id, MarketNewcomerLog.name,
                   MarketNewcomerLog.publisher).order_by(
            MarketNewcomerLog.first_detected_at.desc())
        if lookback and lookback > 0:
            q = q.where(MarketNewcomerLog.first_detected_at >= now - timedelta(days=lookback))
        rows = (await db.execute(q)).all()

        # SLG 门控（ADR 0002 范围 = 竞品 SLG 新品）：只对 SLG 新品搜视频，砍掉非 SLG
        # 新品（足球/扑克/纸牌/塔防等）的噪声召回 + 省 YT 每日配额。信号双取：
        #  ① 实时 is_slg()（厂商主体内存索引；用 live 而非 log 存档列——存档列是检出
        #     时点快照、永不回写，新接入的 SLG 厂商在旧行会漏，同 #168/#171 陷阱）；
        #  ② subgenre_cn 题材含 'SLG'（LLM 题材分类），救「非追踪厂商但题材确是 SLG」的
        #     真竞品（如 Stronghold Kingdoms / My Lands，is_slg=0 但确是 SLG 游戏）。
        # subgenre_cn 稀疏、可能只标在某一行 → 按 app_id 聚合成集合，而非取 dedup 行的值。
        slg_subgenre_ids = set((await db.execute(
            select(MarketNewcomerLog.app_id).where(
                MarketNewcomerLog.subgenre_cn.like("%SLG%")))).scalars().all())
        #  ③ log 存档 is_slg（按 app_id 聚合）：本地化 publisher 串 live 命不中、但别的
        #     combo 检出时判过 SLG 的行（跨 combo 分裂）——没有这条，误标行连视频都不搜。
        slg_logged_ids = set((await db.execute(
            select(MarketNewcomerLog.app_id).where(
                MarketNewcomerLog.is_slg.is_(True)).distinct())).scalars().all())
        # 人工忽略名单先行（与 newcomer_i18n / digest 缺口同名单同口径）：人工裁决的
        # 非 SLG 厂商/单品**不搜**——即使其行在忽略前已被 LLM 写过含 SLG 的
        # subgenre_cn，也不让题材救回复活它（review #181 发现：救回信号缺人工裁决前置）。
        from app.services.newcomers import _is_ignored, _load_ignore_keys
        ignore_pub_keys, ignore_app_ids = await _load_ignore_keys()

        seen: set[str] = set()
        for app_id, name, publisher in rows:
            if app_id in already or app_id in seen:
                continue
            seen.add(app_id)
            if _is_ignored(app_id, publisher, ignore_pub_keys, ignore_app_ids):
                continue
            # 非 SLG 新品跳过：不搜、不记台账——留待后续（厂商接入 / 题材分类）下轮再评估。
            if not (is_slg(app_id, publisher) or app_id in slg_subgenre_ids
                    or app_id in slg_logged_ids):
                continue
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
