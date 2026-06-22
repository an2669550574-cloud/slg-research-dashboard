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
    # 钉钉推送不再随单 combo 各发各的——异动 + 新品并入 03:00 UTC 的日级
    # 情报汇总（_run_daily_alert_digest），一天一条卡，见 release_alerts。
    # 新面孔检出沉淀：落库 + 免费源富化（零 ST），手动 refresh 不经此路径。
    from app.services.newcomer_log import record_market_newcomers
    try:
        await record_market_newcomers(country, platform)
    except Exception:
        logger.exception(
            "Newcomer log record failed for %s/%s (sync itself succeeded)",
            country, platform,
        )


async def _run_daily_alert_digest() -> None:
    """定时任务包装：每日情报汇总（竞品异动 + 两层新品，全 combo 一条钉钉卡）。
    纯本地库重跑检测，零配额；未配 webhook 静默 no-op；异常不拖垮 scheduler。"""
    from app.services.release_alerts import send_daily_digest
    try:
        await send_daily_digest()
    except Exception:
        logger.exception("Daily alert digest job crashed")


async def _run_wechat_login_check() -> None:
    """定时任务包装：微信公众号登录将过期/已失效 → 钉钉提醒重新扫码。
    未启用 / 未配 webhook / 服务连不上 → 静默；异常不拖垮 scheduler。"""
    from app.services.release_alerts import alert_wechat_login_if_needed
    try:
        await alert_wechat_login_if_needed()
    except Exception:
        logger.exception("WeChat login check job crashed")


async def _run_rank_backfill() -> None:
    """定时任务包装：回填异常不能拖垮 scheduler。异常走 logger.exception
    (ERROR→Sentry)。任务自身已含 enabled/mock/配额护栏。"""
    from app.services.rank_backfill import backfill_rank_history
    try:
        await backfill_rank_history()
    except Exception:
        logger.exception("Rank backfill job crashed")


async def _run_newcomer_log_prune() -> None:
    """定时任务包装：清理超龄检出日志（market_newcomer_log 只增不减）。
    任务自带 retention<=0 关闭护栏；异常不拖垮 scheduler。"""
    from app.services.newcomer_log import prune_newcomer_log
    try:
        await prune_newcomer_log()
    except Exception:
        logger.exception("Newcomer log prune job crashed")


async def _run_itunes_releases_sync() -> None:
    """定时任务包装：应用商店开发者清单 diff（iOS 免费 iTunes API + GP 免费
    开发者页，零 ST 配额）。两侧各自含 mock/空账号护栏，异常互不拖垮、
    不拖垮 scheduler；告警窗口各自独立不重报。"""
    from app.services.gp_releases import sync_gp_releases
    from app.services.itunes_releases import sync_itunes_releases
    try:
        await sync_itunes_releases()
    except Exception:
        logger.exception("iTunes releases sync job crashed")
    try:
        await sync_gp_releases()
    except Exception:
        logger.exception("GP releases sync job crashed")


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

    # 每日情报汇总：03:00 UTC = 北京 11:00（核心同步 02:30~02:38 已结束）。
    # 对全部 combo 重跑检测拼一张卡，只发一条；当天没新快照的 combo 被
    # as_of/today_missing 闸门排除，不会重报旧数据。
    scheduler.add_job(
        _run_daily_alert_digest,
        CronTrigger(hour=3, minute=0, timezone="UTC"),
        id="daily_alert_digest",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # 微信公众号登录过期提醒：每日 02:55 UTC（日报前 5 分钟），失效/将过期才发。
    # 任务自带 enabled/webhook/连通性护栏，未启用时空跑无害。
    scheduler.add_job(
        _run_wechat_login_check,
        CronTrigger(hour=2, minute=55, timezone="UTC"),
        id="wechat_login_check",
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

    # 检出日志保留清理：03:45 UTC（回填 03:30 之后、DB 备份 04:00 之前）。
    # market_newcomer_log 检出即落库、只增不减，每日删超过 NEWCOMER_LOG_RETENTION_DAYS
    # 的老行；retention<=0 时任务空跑无害，故无条件挂。
    scheduler.add_job(
        _run_newcomer_log_prune,
        CronTrigger(hour=3, minute=45, timezone="UTC"),
        id="newcomer_log_prune",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # App Store 开发者清单 diff：每日一轮，北京时间 09:00（= 01:00 UTC，避开
    # 04:00 UTC 的 DB 备份窗口、落在上班时段）。原为每 6 小时一轮（01/07/13/19），
    # 按用户反馈推送过频，改自然日一次：检出滞后 ≤24h 可接受，免费 iTunes/GP API、
    # 零 ST 配额；任务自带 mock/空账号护栏，空跑无害。
    scheduler.add_job(
        _run_itunes_releases_sync,
        CronTrigger(hour=9, minute=0, timezone="Asia/Shanghai"),
        id="itunes_releases_sync",
        replace_existing=True,
        misfire_grace_time=3600 * 3,
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
