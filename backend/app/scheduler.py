import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.game import Game, GameRanking
from app.services.sensor_tower import sensor_tower_service

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# 每个 combo 间隔 N 分钟避免 Sensor Tower 端的突发限流；02:30 起步留够
# UTC 早上的低峰窗口。16 个 combo × 2min = 32min → 03:02 结束，仍在窗口内。
_SCHEDULE_START_MINUTE = 30
_SCHEDULE_STEP_MINUTES = 2


async def sync_daily_rankings(country: str = "US", platform: str = "ios") -> int:
    """每日抓取榜单数据并入库 game_rankings。返回写入条数。

    幂等：先 DELETE 同 (date, country, platform) 的行，再 INSERT。
    联合 unique (app_id, date, country, platform) 兜底：两个 scheduler 并发跑时
    后到的会 IntegrityError，回滚后日志告警——好过偷偷写重复。

    关键防线：抓取异常或返回空时，**绝不执行 DELETE**。否则一次 Sensor Tower
    抖动 / 配额耗尽 / 网络故障就会把当天数据删掉又写 0 行——图表静默断档且
    无人知晓。这两种情况都打 logger.error（经 LoggingIntegration 自动进
    Sentry 告警），并保留已有数据原样不动。
    """
    today = utcnow_naive().strftime("%Y-%m-%d")
    try:
        rankings = await sensor_tower_service.get_all_rankings_today(country=country, platform=platform)
    except Exception as e:
        logger.error(
            "Daily rankings sync FAILED to fetch %s/%s on %s — existing rows kept untouched: %s",
            country, platform, today, e, exc_info=True,
        )
        return 0

    if not rankings:
        logger.error(
            "Daily rankings sync got EMPTY result for %s/%s on %s — skipping destructive "
            "rewrite, existing rows kept. Check Sensor Tower availability / quota.",
            country, platform, today,
        )
        return 0

    written = 0
    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(GameRanking).where(
                GameRanking.date == today,
                GameRanking.country == country,
                GameRanking.platform == platform,
            )
        )

        for item in rankings:
            app_id = item.get("app_id")
            if not app_id:
                continue
            db.add(GameRanking(
                app_id=app_id,
                date=today,
                rank=item.get("rank"),
                downloads=item.get("downloads"),
                revenue=item.get("revenue"),
                country=country,
                platform=platform,
                name=item.get("name"),
                publisher=item.get("publisher"),
                icon_url=item.get("icon_url"),
            ))
            written += 1
        if written == 0:
            # 非空响应但全是无 app_id 的脏数据：DELETE 已发出但还没 commit，
            # 回滚即可让当天旧行毫发无损，不留"删了又没写"的空窗。
            await db.rollback()
            logger.error(
                "Daily rankings sync for %s/%s on %s: %d items but none usable "
                "(missing app_id) — rolled back, existing rows kept.",
                country, platform, today, len(rankings),
            )
            return 0
        try:
            await db.commit()
        except IntegrityError as e:
            await db.rollback()
            logger.warning(
                "Daily rankings sync conflict for %s/%s on %s (concurrent run?): %s",
                country, platform, today, e,
            )
            return 0
    logger.info("Daily rankings sync: wrote %d rows for %s/%s on %s", written, country, platform, today)
    return written


async def sync_seed_games_if_empty() -> None:
    """若 games 表为空则从 mock 数据建立一个起始集，避免 dashboard 空数据。"""
    from app.services.sensor_tower import MOCK_SLG_GAMES
    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(Game))
        if existing.scalars().first():
            return
        for g in MOCK_SLG_GAMES:
            db.add(Game(**{k: v for k, v in g.items() if k in Game.__table__.columns.keys()}))
        await db.commit()
        logger.info("Seeded %d games on first run", len(MOCK_SLG_GAMES))


def start_scheduler() -> None:
    if scheduler.running:
        return
    combos = settings.sync_combos_list
    if not combos:
        logger.warning("SYNC_RANKING_COMBOS produced 0 valid combos; scheduler has no jobs")
        scheduler.start()
        return

    for idx, (country, platform) in enumerate(combos):
        # 错峰：02:30, 02:32, 02:34, ... 每个 combo 间隔 2 分钟
        minute_total = _SCHEDULE_START_MINUTE + idx * _SCHEDULE_STEP_MINUTES
        hour = 2 + minute_total // 60
        minute = minute_total % 60
        scheduler.add_job(
            sync_daily_rankings,
            CronTrigger(hour=hour, minute=minute, timezone="UTC"),
            id=f"sync_daily_rankings_{country.lower()}_{platform}",
            kwargs={"country": country, "platform": platform},
            replace_existing=True,
            misfire_grace_time=3600,
        )
    scheduler.start()
    logger.info(
        "Scheduler started with %d jobs (combos: %s)",
        len(scheduler.get_jobs()),
        ", ".join(f"{c}/{p}" for c, p in combos),
    )


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
