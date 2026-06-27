"""tracked iOS 竞品分地区上架日采集（需求② 子项③ / ADR 0004）。

对每个 platform='ios' 且有可用 trackId 的 tracked game，在 REGION_LAUNCH_STOREFRONTS
列的各 storefront 查 iTunes releaseDate（零 ST，每 country 一次批量 lookup、含全部
trackId）→ upsert game_region_release(app_id, country, release_date)。

- releaseDate 随 country 分地区不同 = 分地区上线对照（在哪些区先上 / soft-launch 区序）。
- resultCount=0（该区查不到）也落一行记 release_date=NULL：与「该区是另一个 trackId」
  区分不开，诚实留空，不臆测「未上线」。
- trackId 解析复用 version_tracker._track_id（ios_track_id 优先、否则数字 app_id、
  GP 包名无 trackId 则跳过）；Android 无可靠上架日源 → 仅 iOS（与版本追踪同取舍）。
- 数据近静态（上架日不变），周级 job 刷新即可；refresh 会捕捉竞品「新进某区」。
"""
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.game import Game, GameRegionRelease
from app.models.history import GameHistory
from app.services.appstore import fetch_apps_bulk
from app.services.version_tracker import _track_id

logger = logging.getLogger(__name__)


def _region_event_title(country: str) -> str:
    """新区上线事件标题（每 (app_id, country) 稳定唯一，用作去重键 + 详情页时间线）。"""
    return f"新区上线 · {country.upper()}"


async def detect_new_region_launches(recent_days: int | None = None,
                                     cap: int | None = None) -> list[dict]:
    """检测 tracked iOS 竞品「新进某区」事件，落 GameHistory + 返回变更（供 digest）。

    判据：game_region_release 里 release_date 落在近 recent_days 天的行（= 近期上架，
    非首填的历史回填）。每条按 (app_id, country) 用 GameHistory(event_type='region_launch')
    去重——已播报过的不再重复。每条变更 = {app_id, name, country, date}。

    与版本追踪同范式（写 GameHistory，详情页时间线天然渲染）；零 ST（纯本地表读）。
    USE_MOCK_DATA 下 no-op。封顶 cap 防极端刷屏。
    """
    if settings.USE_MOCK_DATA:
        return []
    days = recent_days if recent_days is not None else settings.REGION_LAUNCH_RECENT_DAYS
    lim = cap if cap is not None else settings.DIGEST_MAX_ITEMS
    cutoff = (utcnow_naive() - timedelta(days=days)).strftime("%Y-%m-%d")
    changes: list[dict] = []
    async with AsyncSessionLocal() as db:
        # 近期有上架日的分地区行 + 对应 tracked game 名（join games：只播报在册竞品）。
        rows = (await db.execute(
            select(GameRegionRelease.app_id, GameRegionRelease.country,
                   GameRegionRelease.release_date, Game.name)
            .join(Game, Game.app_id == GameRegionRelease.app_id)
            .where(GameRegionRelease.release_date.is_not(None),
                   GameRegionRelease.release_date >= cutoff)
            .order_by(GameRegionRelease.release_date.desc())
        )).all()
        for app_id, country, rel_date, name in rows:
            title = _region_event_title(country)
            # 去重：该 (app_id, country) 已播报过则跳过（event_type+title 唯一定位）。
            seen = (await db.execute(
                select(GameHistory.id).where(
                    GameHistory.app_id == app_id,
                    GameHistory.event_type == "region_launch",
                    GameHistory.title == title,
                ).limit(1))).first()
            if seen:
                continue
            db.add(GameHistory(
                app_id=app_id, event_date=rel_date, event_type="region_launch",
                title=title, description=f"{name} 于 {rel_date} 在 {country.upper()} 区上架",
                source="appstore"))
            changes.append({"app_id": app_id, "name": name,
                            "country": country.upper(), "date": rel_date})
            if len(changes) >= lim:
                break
        await db.commit()
    if changes:
        logger.info("region launch: %d new-region event(s): %s",
                    len(changes), [(c["name"], c["country"]) for c in changes])
    return changes


async def sync_region_launches(storefronts: list[str] | None = None) -> dict:
    """刷新所有 tracked iOS games 的分地区上架日。返回 {games, storefronts, rows}。

    USE_MOCK_DATA / 无 storefront / 无可用 trackId 时整体 no-op。零 ST。
    请求数 = storefront 数（每 country 一次批量 lookup 含全部 trackId），与游戏数无关。
    """
    out = {"games": 0, "storefronts": 0, "rows": 0}
    if settings.USE_MOCK_DATA:
        return out
    stores = (storefronts if storefronts is not None
              else settings.region_launch_storefront_list)
    if not stores:
        return out
    async with AsyncSessionLocal() as db:
        games = (await db.execute(
            select(Game).where(Game.platform == "ios"))).scalars().all()
        # trackId → 业务 app_id（按 app_id 落库，前端按 app_id 聚合分地区行）。
        tid_to_app: dict[str, str] = {}
        for g in games:
            tid = _track_id(g)
            if tid:
                tid_to_app[tid] = g.app_id
        if not tid_to_app:
            return out
        out["games"] = len(tid_to_app)
        out["storefronts"] = len(stores)
        now = utcnow_naive()
        for country in stores:
            bulk = await fetch_apps_bulk(list(tid_to_app), country=country)
            for tid, app_id in tid_to_app.items():
                rel_date = (bulk.get(tid) or {}).get("release_date")
                # 原子 upsert（SQLite ON CONFLICT DO UPDATE）：避免周级 job 与手动
                # POST /regions/sync 并发各自 INSERT 同 (app_id,country) 撞唯一约束 →
                # 端点 500 + 全量回滚。冲突即覆盖 release_date（同源 iTunes 数据，
                # last-writer-wins 无害）+ 刷新 checked_at。沿用 rank_backfill 的写法。
                await db.execute(
                    sqlite_insert(GameRegionRelease)
                    .values(app_id=app_id, country=country,
                            release_date=rel_date, checked_at=now)
                    .on_conflict_do_update(
                        index_elements=["app_id", "country"],
                        set_={"release_date": rel_date, "checked_at": now},
                    )
                )
                out["rows"] += 1
        await db.commit()
    logger.info("region launch sync: %d games × %d storefronts → %d rows",
                out["games"], out["storefronts"], out["rows"])
    return out
