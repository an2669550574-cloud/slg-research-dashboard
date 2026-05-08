"""Sensor Tower API monthly quota + last-known-good snapshot store.

策略：每次成功调用真实 API 之前先 try_consume；超额时跳过 httpx 请求、
回读 sensor_tower_snapshots 里的最后一次成功结果。月份切换是隐式的——
year_month 是主键，新月份自动新插入一行。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


def current_year_month() -> str:
    return datetime.utcnow().strftime("%Y-%m")


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
    return True


async def try_consume() -> bool:
    """尝试占用本月一次 API 调用配额；返回 True 表示允许调用。"""
    limit = settings.SENSOR_TOWER_MONTHLY_LIMIT
    ym = current_year_month()
    async with AsyncSessionLocal() as session:
        return await _consume_in(session, ym, limit)


async def current_usage() -> dict:
    """返回 {year_month, used, limit, remaining, percentage} 供前端展示。"""
    limit = settings.SENSOR_TOWER_MONTHLY_LIMIT
    ym = current_year_month()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT count FROM api_quota_monthly WHERE year_month = :ym").bindparams(ym=ym)
        )
        row = result.first()
        used = row[0] if row else 0
    remaining = max(0, limit - used)
    return {
        "year_month": ym,
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "percentage": round(used / limit * 100, 1) if limit > 0 else 0.0,
        "exhausted": used >= limit,
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
