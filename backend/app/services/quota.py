"""Sensor Tower API monthly quota + last-known-good snapshot store.

策略：每次成功调用真实 API 之前先 try_consume；超额时跳过 httpx 请求、
回读 sensor_tower_snapshots 里的最后一次成功结果。月份切换是隐式的——
year_month 是主键，新月份自动新插入一行。
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive

logger = logging.getLogger(__name__)


def current_year_month() -> str:
    return utcnow_naive().strftime("%Y-%m")


async def _consume_in(session: AsyncSession, ym: str, limit: int) -> bool:
    """UPSERT count+1 并 RETURNING 新值；若超过 limit 则回滚自增并返回 False。"""
    result = await session.execute(
        text(
            "INSERT INTO api_quota_monthly (year_month, count, updated_at) "
            "VALUES (:ym, 1, CURRENT_TIMESTAMP) "
            "ON CONFLICT(year_month) DO UPDATE SET "
            "count = api_quota_monthly.count + 1, updated_at = CURRENT_TIMESTAMP "
            "RETURNING count"
        ).bindparams(ym=ym)
    )
    row = result.first()
    if row is None:
        return False
    new_count = row[0]
    if new_count > limit:
        await session.execute(
            text("UPDATE api_quota_monthly SET count = count - 1 WHERE year_month = :ym").bindparams(ym=ym)
        )
        await session.commit()
        return False
    await session.commit()

    # 边沿触发告警：count 单调 +1，每个阈值每月恰好被等值命中一次 → 不会刷屏。
    # 用 logger.error 而非 warning 是有意的：只有 ERROR 经 LoggingIntegration
    # 进 Sentry，配额在硬上限下逼近耗尽对单人维护是需要主动推送的事件。
    # 跨月时 year_month 换行、count 从 1 重新数，阈值自动重新武装，无需额外状态。
    warn_at = math.ceil(limit * settings.SENSOR_TOWER_QUOTA_WARN_PCT / 100)
    if new_count == limit:
        logger.error(
            "Sensor Tower quota EXHAUSTED for %s (%d/%d) — production will serve "
            "stale snapshots until month rollover.",
            ym, new_count, limit,
        )
    elif new_count == warn_at and warn_at < limit:
        logger.error(
            "Sensor Tower quota crossed %d%% for %s (%d/%d) — alerting before "
            "exhaustion; review SYNC_RANKING_COMBOS / manual-refresh usage.",
            settings.SENSOR_TOWER_QUOTA_WARN_PCT, ym, new_count, limit,
        )
    return True


async def try_consume() -> bool:
    """尝试占用本月一次 API 调用配额；返回 True 表示允许调用。"""
    limit = settings.SENSOR_TOWER_MONTHLY_LIMIT
    ym = current_year_month()
    async with AsyncSessionLocal() as session:
        return await _consume_in(session, ym, limit)


async def current_usage() -> dict:
    """返回 {year_month, used, limit, remaining, percentage, data_source, data_updated_at} 供前端展示。"""
    limit = settings.SENSOR_TOWER_MONTHLY_LIMIT
    ym = current_year_month()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT count FROM api_quota_monthly WHERE year_month = :ym").bindparams(ym=ym)
        )
        row = result.first()
        used = row[0] if row else 0

        # 查最近一次 snapshot 的 updated_at，用于前端展示数据新鲜度
        snap_result = await session.execute(
            text("SELECT MAX(updated_at) FROM sensor_tower_snapshots")
        )
        snap_row = snap_result.first()
        data_updated_at = snap_row[0] if snap_row and snap_row[0] else None
        if data_updated_at is not None:
            data_updated_at = str(data_updated_at)

    exhausted = used >= limit

    # 判定当前数据来源
    if settings.USE_MOCK_DATA:
        data_source = "mock"
    elif exhausted:
        data_source = "snapshot_stale"
    else:
        # 存在 snapshot 但未过期 → 可能从 snapshot 中读取（不消耗配额）
        # 由 _cached_get 的 L2 逻辑决定；这里无法确定单次请求走哪条路，
        # 所以标记为 active（真实 API 可用）
        data_source = "real_api"

    remaining = max(0, limit - used)
    return {
        "year_month": ym,
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "percentage": round(used / limit * 100, 1) if limit > 0 else 0.0,
        "exhausted": exhausted,
        "data_source": data_source,
        "data_updated_at": data_updated_at,
    }


async def save_snapshot(cache_key: str, payload: Any) -> None:
    """成功调用真实 API 后保存一份"最后已知好数据"，超额时降级回读。"""
    payload_json = json.dumps(payload)
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO sensor_tower_snapshots (cache_key, payload, updated_at) "
                "VALUES (:k, :p, CURRENT_TIMESTAMP) "
                "ON CONFLICT(cache_key) DO UPDATE SET "
                "payload = excluded.payload, updated_at = CURRENT_TIMESTAMP"
            ).bindparams(k=cache_key, p=payload_json)
        )
        await session.commit()


async def load_snapshot(cache_key: str) -> Optional[Any]:
    """读取超额降级用的最后快照；不存在时返回 None。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT payload FROM sensor_tower_snapshots WHERE cache_key = :k").bindparams(k=cache_key)
        )
        row = result.first()
        if not row:
            return None
        return json.loads(row[0])


async def delete_snapshot(cache_key: str) -> None:
    """显式清除某个 cache_key 的持久快照。force-refresh 路径用：
    清掉 L2 后再调真实 API，确保下次 _cached_get 不会再看到旧快照。"""
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM sensor_tower_snapshots WHERE cache_key = :k").bindparams(k=cache_key)
        )
        await session.commit()


async def load_snapshot_if_fresh(cache_key: str, max_age_seconds: float) -> Optional[Any]:
    """读取尚在新鲜窗口内的快照；过期或不存在返回 None。

    用于 snapshot-first 缓存路径：内存 TTL miss 时先查 SQLite，命中就直接
    返回，不消耗月度配额。julianday() 在 SQLite 里是天数差，乘 86400 转秒。
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT payload FROM sensor_tower_snapshots "
                "WHERE cache_key = :k "
                "AND (julianday('now') - julianday(updated_at)) * 86400 < :max_age"
            ).bindparams(k=cache_key, max_age=max_age_seconds)
        )
        row = result.first()
        if not row:
            return None
        return json.loads(row[0])
