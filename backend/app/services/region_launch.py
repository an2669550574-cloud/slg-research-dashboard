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

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.game import Game, GameRegionRelease
from app.services.appstore import fetch_apps_bulk
from app.services.version_tracker import _track_id

logger = logging.getLogger(__name__)


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
        # 既有行索引 (app_id, country) → row，便于 upsert（刷新覆盖 release_date）。
        existing = {(r.app_id, r.country): r for r in (await db.execute(
            select(GameRegionRelease))).scalars().all()}
        for country in stores:
            bulk = await fetch_apps_bulk(list(tid_to_app), country=country)
            for tid, app_id in tid_to_app.items():
                rel_date = (bulk.get(tid) or {}).get("release_date")
                key = (app_id, country)
                row = existing.get(key)
                if row is None:
                    row = GameRegionRelease(app_id=app_id, country=country,
                                            release_date=rel_date)
                    db.add(row)
                    existing[key] = row
                else:
                    row.release_date = rel_date
                    row.checked_at = utcnow_naive()
                out["rows"] += 1
        await db.commit()
    logger.info("region launch sync: %d games × %d storefronts → %d rows",
                out["games"], out["storefronts"], out["rows"])
    return out
