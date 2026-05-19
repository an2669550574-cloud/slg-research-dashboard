"""历史排名回填：每晚日常同步后涓流补一点。

为什么这样做：Sensor Tower **没有"某 app 排名历史"接口**，只能逐
(combo, 日期) 拉整张品类榜（1 调用/日/组合）从有序列表读名次；下载/收入
那种"一次 range 批量"在排名上不存在。故全量日粒度补 1 年 ×4 组合 ≈ 1460
次配额（≈3 个月全部预算），不可行。改**周粒度**（rank 长期趋势够看）：
4 组合 × 52 周 ≈ 208 次一次性，再用护栏 + 每日预算涓流摊到数周补完，
**永不挤占核心日同步**。

幂等/可续/自停：进度直接由 game_rankings 现有 rank 行推断——某 (combo,
锚点日) 已有 rank IS NOT NULL 的行即视为已补，跳过；全部补完后本任务自然
空转。**只 UPSERT rank，绝不 DELETE**：同日可能已有"销量回填行"
(rank=NULL，承载 1 年收入/下载历史)，删了趋势图收入/下载会断档。
"""
import logging
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.game import GameRanking
from app.services import quota
from app.services.sensor_tower import sensor_tower_service

logger = logging.getLogger(__name__)


async def _week_done(db, country: str, platform: str, date: str) -> bool:
    """该 (combo, 日) 是否已有名次行。用 rank IS NOT NULL 判定——销量回填行
    rank=NULL 不算数，否则会误判"已补"导致排名永远补不进去。"""
    r = await db.execute(
        text(
            "SELECT 1 FROM game_rankings WHERE country=:c AND platform=:p "
            "AND date=:d AND rank IS NOT NULL LIMIT 1"
        ).bindparams(c=country, p=platform, d=date)
    )
    return r.first() is not None


async def _merge_rank_rows(db, country: str, platform: str, date: str, rows: list[dict]) -> int:
    """UPSERT 仅写 rank，不碰已有行的 downloads/revenue（保住销量历史）。"""
    written = 0
    for r in rows:
        aid, rk = r.get("app_id"), r.get("rank")
        if not aid or rk is None:
            continue
        await db.execute(
            sqlite_insert(GameRanking)
            .values(app_id=aid, date=date, rank=rk, country=country, platform=platform)
            .on_conflict_do_update(
                index_elements=["app_id", "date", "country", "platform"],
                set_={"rank": rk},
            )
        )
        written += 1
    return written


async def backfill_rank_history() -> int:
    """每晚涓流补历史排名（最新周优先）。返回本次合并的行数。

    护栏：当月剩余配额 ≤ RANK_BACKFILL_QUOTA_FLOOR 当晚整体跳过；每晚最多
    RANK_BACKFILL_DAILY_BUDGET 次真实拉取；每次拉取前再核一次护栏，
    多调用累积也不击穿底线。
    """
    if not settings.RANK_BACKFILL_ENABLED or settings.USE_MOCK_DATA:
        return 0
    combos = settings.sync_combos_list
    if not combos:
        return 0

    usage = await quota.current_usage()
    if usage["remaining"] <= settings.RANK_BACKFILL_QUOTA_FLOOR:
        logger.info(
            "Rank backfill skipped: quota remaining %d <= floor %d (protecting core sync)",
            usage["remaining"], settings.RANK_BACKFILL_QUOTA_FLOOR,
        )
        return 0

    today = utcnow_naive().date()
    budget = settings.RANK_BACKFILL_DAILY_BUDGET
    spent = 0
    total_written = 0

    # 最新周优先：趋势图从"现在"向过去逐步长出来，近期数据先到位。
    for k in range(1, settings.RANK_BACKFILL_WEEKS + 1):
        if spent >= budget:
            break
        date = (today - timedelta(days=7 * k)).strftime("%Y-%m-%d")
        for country, platform in combos:
            if spent >= budget:
                break
            async with AsyncSessionLocal() as db:
                if await _week_done(db, country, platform, date):
                    continue
            # 每次真实拉取前复核护栏：本轮多调用累积也不能击穿底线。
            usage = await quota.current_usage()
            if usage["remaining"] <= settings.RANK_BACKFILL_QUOTA_FLOOR:
                logger.info("Rank backfill stop mid-run: quota floor reached.")
                return total_written
            try:
                rows = await sensor_tower_service.get_ranking_on_date(country, platform, date)
            except Exception:
                logger.exception(
                    "Rank backfill fetch failed for %s/%s %s", country, platform, date
                )
                spent += 1  # 真实尝试过，计入预算，避免坏组合当晚反复打
                continue
            spent += 1  # 注：命中 L2 快照其实没烧配额，这里保守计数（宁少补不超烧）
            if not rows:
                logger.warning(
                    "Rank backfill empty chart for %s/%s %s", country, platform, date
                )
                continue
            async with AsyncSessionLocal() as db:
                w = await _merge_rank_rows(db, country, platform, date, rows)
                await db.commit()
            total_written += w
            logger.info(
                "Rank backfill %s/%s %s: merged %d ranks", country, platform, date, w
            )

    if spent:
        logger.info(
            "Rank backfill run done: %d fetches, %d rows merged", spent, total_written
        )
    return total_written
