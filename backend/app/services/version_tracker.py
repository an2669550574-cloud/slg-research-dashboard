"""tracked iOS games 版本变更追踪（需求② / ADR 0003）。

日级重查 tracked 竞品的 iTunes 版本号（零 ST，复用 appstore.fetch_apps_bulk），
与 Game.version 比对：
- 首次（Game.version 为 NULL）：填基线、**不算变更**（no_baseline，与新品检测同哲学，
  避免上线即把所有 app 当「刚更新」刷屏）。
- 版本变了：写一条 game_histories(event_type='version') 变更事件 + 更新 Game 当前值，
  并把变更收集返回——供每日 digest「版本更新」段（切片 B）读当天事件展示。
Android 无版本源（GP 页 JSON-LD 无 version）→ 只查 platform='ios' 的 tracked games。
"""
import logging

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.game import Game
from app.models.history import GameHistory
from app.services.appstore import fetch_apps_bulk

logger = logging.getLogger(__name__)


def _numeric(app_id: str) -> str:
    """Game.app_id 归一成纯数字 trackId（去掉可能的 'id' 前缀）喂 iTunes lookup。"""
    return (app_id or "").replace("id", "").strip()


async def check_tracked_versions() -> list[dict]:
    """重查 tracked iOS games 版本，检测变更并落库。返回变更列表（供 digest）。

    每条变更 = {app_id, name, old, new, date}。USE_MOCK_DATA 下整体 no-op
    （不打真 iTunes），与新品富化同哲学。
    """
    if settings.USE_MOCK_DATA:
        return []
    changes: list[dict] = []
    async with AsyncSessionLocal() as db:
        games = (await db.execute(
            select(Game).where(Game.platform == "ios"))).scalars().all()
        # 归一成纯数字 trackId 喂批量 lookup；反查表回到 Game 行。
        id_map = {_numeric(g.app_id): g for g in games if _numeric(g.app_id).isdigit()}
        if not id_map:
            return []
        info = await fetch_apps_bulk(list(id_map.keys()), country="us")
        today = utcnow_naive().strftime("%Y-%m-%d")
        for nid, g in id_map.items():
            cur = info.get(nid)
            if not cur or not cur.get("version"):
                continue  # 查不到 / 无版本号 → 保持原值，不动
            new_v = cur["version"]
            new_d = cur.get("current_version_date") or today
            if g.version is None:
                g.version, g.version_date = new_v, new_d  # 基线，不算变更
                continue
            if new_v != g.version:
                old_v = g.version
                db.add(GameHistory(
                    app_id=g.app_id, event_date=new_d, event_type="version",
                    title=f"版本更新 {old_v} → {new_v}",
                    description=(cur.get("release_notes") or "")[:1000] or None,
                    source="appstore"))
                g.version, g.version_date = new_v, new_d
                changes.append({"app_id": g.app_id, "name": g.name,
                                "old": old_v, "new": new_v, "date": new_d})
        await db.commit()
    if changes:
        logger.info("version tracker: %d change(s): %s",
                    len(changes), [c["app_id"] for c in changes])
    return changes
