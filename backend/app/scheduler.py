import logging
from datetime import date
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
# UTC 早上的低峰窗口。6 个 combo × 2min = 12min → 02:42 结束，仍在窗口内。
_SCHEDULE_START_MINUTE = 30
_SCHEDULE_STEP_MINUTES = 2


def _due_by_interval(today: date, interval_days: int) -> bool:
    """按 UTC 日序号取模判定"今天是否到点"。interval<=1 → 永远到点（每天）。

    纯函数、无状态：用 date.toordinal() % interval 而非持久化游标，
    跨进程重启/多副本判定一致，无需协调。interval=2 → 隔日；7 → 每 7 天。
    """
    if interval_days <= 1:
        return True
    return today.toordinal() % interval_days == 0


def _combo_due_today(country: str, platform: str, today: date) -> bool:
    """该 combo 今天是否应同步：主市场按 SYNC_PRIMARY_INTERVAL_DAYS，
    次市场按 SYNC_SECONDARY_INTERVAL_DAYS。默认都按周。"""
    if (country, platform) in settings.sync_primary_combos_set:
        return _due_by_interval(today, settings.SYNC_PRIMARY_INTERVAL_DAYS)
    return _due_by_interval(today, settings.SYNC_SECONDARY_INTERVAL_DAYS)


def _sales_due_today(today: date) -> bool:
    """今天是否抓销量：按 SALES_FETCH_INTERVAL_DAYS。非抓取日榜行 dl/rev 留 NULL。"""
    return _due_by_interval(today, settings.SALES_FETCH_INTERVAL_DAYS)


async def sync_daily_rankings(country: str = "US", platform: str = "ios", with_sales: bool = True) -> int:
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
        rankings = await sensor_tower_service.get_all_rankings_today(
            country=country, platform=platform, with_sales=with_sales)
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


async def _scheduled_sync(country: str = "US", platform: str = "ios") -> None:
    """定时任务专用包装：同步落库后做竞品异动检测。

    手动 refresh / trigger 走裸 sync_daily_rankings（不经这里），否则每次
    手动刷新都触发告警刷屏。检测失败不能拖垮 sync —— 单独 try 兜住，
    异常走 logger.exception（ERROR→Sentry，让坏掉的检测器自己可见）。
    mock 模式数据是随机噪声，告警无意义 → 跳过。

    配额分级：次市场 combo 非到点日整轮跳过（零配额）；销量只对**主市场**抓——
    次市场永远 with_sales=False（JP/KR 销量改走详情页按需取），主市场再叠加
    SALES_FETCH_INTERVAL_DAYS 的非抓取日 False（省 top-N 批量销量那 1 次配额，
    榜行 dl/rev 留 NULL，日榜读路径用上次已知值兜底）。手动路径不经这里，永远全量。
    """
    today = utcnow_naive().date()
    if not _combo_due_today(country, platform, today):
        logger.info("Skipping %s/%s today: secondary market, not due (interval=%d days)",
                    country, platform, settings.SYNC_SECONDARY_INTERVAL_DAYS)
        return
    is_primary = (country, platform) in settings.sync_primary_combos_set
    with_sales = is_primary and _sales_due_today(today)
    written = await sync_daily_rankings(country=country, platform=platform, with_sales=with_sales)
    if not written or settings.USE_MOCK_DATA:
        return
    from app.services.movement import detect_and_alert_movement
    today = utcnow_naive().strftime("%Y-%m-%d")
    try:
        await detect_and_alert_movement(country, platform, today)
    except Exception:
        logger.exception(
            "Competitor movement check failed for %s/%s on %s (sync itself succeeded)",
            country, platform, today,
        )
    # 新品两层（全市场空降 + 厂商任意名次）→ 钉钉。新快照落库的这次同步正好是
    # 新面孔的"首报"窗口，天然去重；未配 webhook 时函数内静默 no-op。
    from app.services.release_alerts import alert_chart_newcomers
    try:
        await alert_chart_newcomers(country, platform)
    except Exception:
        logger.exception(
            "Newcomer DingTalk alert failed for %s/%s (sync itself succeeded)",
            country, platform,
        )


async def _run_rank_backfill() -> None:
    """定时任务包装：回填异常不能拖垮 scheduler。异常走 logger.exception
    (ERROR→Sentry)。任务自身已含 enabled/mock/配额护栏。"""
    from app.services.rank_backfill import backfill_rank_history
    try:
        await backfill_rank_history()
    except Exception:
        logger.exception("Rank backfill job crashed")


async def _run_itunes_releases_sync() -> None:
    """定时任务包装：App Store 开发者清单 diff（免费 iTunes API、零 ST 配额）。
    任务自身已含 mock/空账号护栏，异常不拖垮 scheduler。"""
    from app.services.itunes_releases import sync_itunes_releases
    try:
        await sync_itunes_releases()
    except Exception:
        logger.exception("iTunes releases sync job crashed")


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


async def seed_tag_dimensions_if_empty() -> None:
    """若标签库为空则建一组起步标签（对齐需求测试用例）：

    - 投放时间（date 型，必选）
    - 路型（text 型）：1路 / 2路 / 3路 / 4路
    仅在 tag_dimensions 全空时插入，避免覆盖用户已维护的标签库。
    """
    from app.models.tag import TagDimension, TagOption
    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(TagDimension))
        if existing.scalars().first():
            return
        launch = TagDimension(name="投放时间", value_type="date", is_required=True, allow_multi=False, sort_order=0)
        road = TagDimension(name="路型", value_type="text", allow_multi=True, sort_order=1)
        db.add_all([launch, road])
        await db.flush()
        for i, v in enumerate(["1路", "2路", "3路", "4路"]):
            db.add(TagOption(dimension_id=road.id, value=v, sort_order=i))
        await db.commit()
        logger.info("Seeded starter tag dimensions (投放时间 / 路型) on first run")


async def seed_publishers_if_empty() -> None:
    """publisher_entities 空表时灌入内置起步种子（slg_publishers.SEED_PUBLISHERS）。

    幂等：表非空即跳过，绝不覆盖用户已维护的主体 / 马甲。种子的 keyword/app_id
    全集与迁移前硬编码白名单一致，首次启动后 is_slg 行为不变。
    """
    from app.models.publisher import PublisherEntity, PublisherAlias, PublisherAppId
    from app.services.slg_publishers import SEED_PUBLISHERS
    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(PublisherEntity))
        if existing.scalars().first():
            return
        for i, p in enumerate(SEED_PUBLISHERS):
            e = PublisherEntity(
                name=p["name"], name_en=p["name_en"], hq_region=p["hq_region"],
                is_slg=p["is_slg"], brief=p["brief"], sort_order=i,
            )
            db.add(e)
            await db.flush()  # 拿 e.id 再挂子行
            for kw, label in p["aliases"]:
                db.add(PublisherAlias(entity_id=e.id, keyword=kw, label=label))
            for aid, note in p["app_ids"]:
                db.add(PublisherAppId(entity_id=e.id, app_id=aid, note=note))
        await db.commit()
        logger.info("Seeded %d publisher entities on first run", len(SEED_PUBLISHERS))


def start_scheduler() -> None:
    if scheduler.running:
        return
    combos = settings.sync_combos_list
    if not combos:
        if not settings.USE_MOCK_DATA:
            # 真实数据部署却 0 个同步组合 = 线上永远不更新榜单却假装健康。
            # 与 main.py 的 API_KEY 守卫同理，启动即拒绝，别静默裸奔。
            raise RuntimeError(
                "SYNC_RANKING_COMBOS produced 0 valid combos while USE_MOCK_DATA=False "
                "— refusing to start a real-data deployment that would never sync rankings."
            )
        logger.warning("SYNC_RANKING_COMBOS produced 0 valid combos; scheduler has no jobs (mock mode)")
        scheduler.start()
        return

    for idx, (country, platform) in enumerate(combos):
        # 错峰：02:30, 02:32, 02:34, ... 每个 combo 间隔 2 分钟
        minute_total = _SCHEDULE_START_MINUTE + idx * _SCHEDULE_STEP_MINUTES
        hour = 2 + minute_total // 60
        minute = minute_total % 60
        scheduler.add_job(
            _scheduled_sync,
            CronTrigger(hour=hour, minute=minute, timezone="UTC"),
            id=f"sync_daily_rankings_{country.lower()}_{platform}",
            kwargs={"country": country, "platform": platform},
            replace_existing=True,
            misfire_grace_time=3600,
        )

    # 历史排名回填：03:30 UTC（核心同步 02:30~02:38 已结束，DB 备份 04:00
    # 之前）。任务内部自带 enabled/mock/配额护栏，空跑也无害，故无条件挂。
    scheduler.add_job(
        _run_rank_backfill,
        CronTrigger(hour=3, minute=30, timezone="UTC"),
        id="rank_backfill",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # App Store 开发者清单 diff：周一 05:00 UTC（DB 备份 04:00 之后）。
    # 免费 iTunes lookup API、零 ST 配额；任务自带 mock/空账号护栏，空跑无害。
    scheduler.add_job(
        _run_itunes_releases_sync,
        CronTrigger(day_of_week="mon", hour=5, minute=0, timezone="UTC"),
        id="itunes_releases_sync",
        replace_existing=True,
        misfire_grace_time=3600 * 6,
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
