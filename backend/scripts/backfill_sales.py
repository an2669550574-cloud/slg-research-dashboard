"""一次性历史回填：把每个市场组合 Top-N SLG 竞品过去 N 天的**收入/下载**
日序列从 Sensor Tower sales_report_estimates 拉回，永久写入 game_rankings。

为什么只回填收入/下载：sales_report_estimates 是「区间 + 日粒度 + 多 app
批量」端点，一次调用拿一大段，配额极省。排名没有这种历史端点（ranking 是
单日快照），只能靠 scheduler 每天前向累积——故本脚本不碰 rank。

幂等：ON CONFLICT(app_id,date,country,platform) 只更新 downloads/revenue，
不动 scheduler 已写入的 rank/name/publisher。可安全重跑、断点续跑。
配额：每个 (combo, 日期分片) 1 次调用，按月度配额计；耗尽即停（已拉到的
照常入库），不会突破 500/月硬顶。

运行：容器内 `python -m scripts.backfill_sales`
"""
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.database import AsyncSessionLocal
from app.models.game import GameRanking
from app.services import quota
from app.services.sensor_tower import sensor_tower_service
from app.services.slg_publishers import is_slg
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_sales")

LOOKBACK_DAYS = 365
TOP_N = 50
CHUNK_DAYS = 90  # 单次 sales 调用的最大区间；ST 端可能对跨度有上限，分片更稳


def _date_chunks(start: datetime, end: datetime, size: int):
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=size - 1), end)
        yield cur.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")
        cur = chunk_end + timedelta(days=1)


async def _top_slg_ids(db, country: str, platform: str) -> list[str]:
    """该组合最近一天榜单里的 Top-N SLG app_id（按 rank 升序）。"""
    latest = (await db.execute(
        select(func.max(GameRanking.date)).where(
            GameRanking.country == country, GameRanking.platform == platform,
            GameRanking.rank.isnot(None),
        )
    )).scalar_one_or_none()
    if not latest:
        return []
    rows = (await db.execute(
        select(GameRanking).where(
            GameRanking.country == country, GameRanking.platform == platform,
            GameRanking.date == latest, GameRanking.rank.isnot(None),
        ).order_by(GameRanking.rank)
    )).scalars().all()
    ids = [r.app_id for r in rows if is_slg(r.app_id, r.publisher)]
    return ids[:TOP_N]


async def _upsert(db, country: str, platform: str, series: dict) -> int:
    """series: {app_id: {date: {downloads, revenue}}} → game_rankings 幂等写入。"""
    payload = [
        {"app_id": aid, "date": d, "country": country, "platform": platform,
         "downloads": v["downloads"], "revenue": v["revenue"]}
        for aid, days in series.items() for d, v in days.items()
    ]
    if not payload:
        return 0
    base = sqlite_insert(GameRanking)
    stmt = base.on_conflict_do_update(
        index_elements=["app_id", "date", "country", "platform"],
        set_={"downloads": base.excluded.downloads, "revenue": base.excluded.revenue},
    )
    await db.execute(stmt, payload)
    await db.commit()
    return len(payload)


async def main() -> None:
    if sensor_tower_service.use_mock:
        logger.error("USE_MOCK_DATA / 无 API key — 回填无意义，已退出")
        return

    end = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=LOOKBACK_DAYS)
    before = await quota.current_usage()
    logger.info("配额起点: %s", before)

    combos = settings.sync_combos_list
    total_rows = 0
    aborted = False
    for country, platform in combos:
        if aborted:
            break
        async with AsyncSessionLocal() as db:
            ids = await _top_slg_ids(db, country, platform)
            if not ids:
                logger.warning("%s/%s 无榜单数据，跳过", country, platform)
                continue
            logger.info("%s/%s: 回填 %d 个 SLG，%s..%s",
                        country, platform, len(ids),
                        start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            merged: dict = {}
            for ws, we in _date_chunks(start, end, CHUNK_DAYS):
                series = await sensor_tower_service.fetch_sales_series(
                    ids, country, platform, ws, we)
                if series is None:  # 配额耗尽 → 停整轮，已合并的照常入库
                    logger.warning("配额耗尽，停止后续抓取（已抓部分照常入库）")
                    aborted = True
                    break
                for aid, days in series.items():
                    merged.setdefault(aid, {}).update(days)
                logger.info("  %s..%s -> %d apps", ws, we, len(series))
            n = await _upsert(db, country, platform, merged)
            total_rows += n
            logger.info("%s/%s: upsert %d 行", country, platform, n)

    after = await quota.current_usage()
    logger.info("完成: 共 upsert %d 行；配额 %s -> %s", total_rows, before, after)


if __name__ == "__main__":
    asyncio.run(main())
