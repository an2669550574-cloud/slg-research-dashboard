import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.game import Game, GameRanking
from app.services.sensor_tower import sensor_tower_service

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def sync_daily_rankings(country: str = "US", platform: str = "ios") -> int:
    """每日抓取榜单数据并入库 game_rankings。返回写入条数。

    幂等：先 DELETE 同 (date, country, platform) 的行，再 INSERT。
    联合 unique (app_id, date, country, platform) 兜底：两个 scheduler 并发跑时
    后到的会 IntegrityError，回滚后日志告警——好过偷偷写重复。
    """
    today = utcnow_naive().strftime("%Y-%m-%d")
    rankings = await sensor_tower_service.get_all_rankings_today(country=country, platform=platform)

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
    # 每日 02:30 UTC 抓 US/iOS 与 US/android 榜单
    scheduler.add_job(
        sync_daily_rankings,
        CronTrigger(hour=2, minute=30, timezone="UTC"),
        id="sync_daily_rankings_us_ios",
        kwargs={"country": "US", "platform": "ios"},
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        sync_daily_rankings,
        CronTrigger(hour=2, minute=35, timezone="UTC"),
        id="sync_daily_rankings_us_android",
        kwargs={"country": "US", "platform": "android"},
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
