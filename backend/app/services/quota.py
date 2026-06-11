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

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive

logger = logging.getLogger(__name__)

# Sensor Tower /v1/api_usage 缓存键。下划线前缀确保不会与任何业务 cache_key
# (sales:.../today:.../rankhist:...) 冲撞。同表复用，无需新建迁移。
ACCOUNT_USAGE_KEY = "__sys:account_usage__"


def current_year_month() -> str:
    return utcnow_naive().strftime("%Y-%m")


def current_date_utc() -> str:
    return utcnow_naive().strftime("%Y-%m-%d")


async def _consume_in(session: AsyncSession, ym: str, limit: int) -> bool:
    """UPSERT 月度 count+1 并 RETURNING 新值；超过 limit 则回滚自增返 False；
    否则同事务再 UPSERT 当天 daily count+1 后提交。
    daily 记录是仪表盘"配额历史曲线"的源,与月度严格同步——拒绝路径绝不动 daily,
    成功路径两者同时 +1。"""
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
    # 月度成功后才记 daily,保持二者一致(被拒绝的不算)
    today = current_date_utc()
    await session.execute(
        text(
            "INSERT INTO api_quota_daily (date, count, updated_at) "
            "VALUES (:d, 1, CURRENT_TIMESTAMP) "
            "ON CONFLICT(date) DO UPDATE SET "
            "count = api_quota_daily.count + 1, updated_at = CURRENT_TIMESTAMP"
        ).bindparams(d=today)
    )
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


async def _org_remaining_cached() -> Optional[int]:
    """读已缓存的 ST 账户用量算出公司池剩余（不打网络）。

    走 load_snapshot 而非 get_account_usage：try_consume 是热路径，绝不能因为
    查公司池又触发一次 live 拉取。如果缓存压根没有（mock 模式 / 启动后还没人
    打开过 dashboard），返回 None，调用方按"不知道"处理（不限流，保守放行）。
    """
    snap = await load_snapshot(ACCOUNT_USAGE_KEY)
    if snap is None:
        return None
    org = (snap.get("organization") or {})
    usage, limit = org.get("usage"), org.get("limit")
    if not isinstance(usage, int) or not isinstance(limit, int) or limit <= 0:
        return None
    return max(0, limit - usage)


def _classify_state(remaining: Optional[int]) -> str:
    """根据公司池剩余分类 normal / low / reserved。

    None → normal（无信息，保守放行，等下一次 dashboard 拉到再分类）。
    """
    if remaining is None:
        return "normal"
    if remaining <= settings.SENSOR_TOWER_ORG_RESERVE:
        return "reserved"
    if remaining <= settings.SENSOR_TOWER_ORG_LOW_THRESHOLD:
        return "low"
    return "normal"


async def try_consume() -> bool:
    """尝试占用本月一次 API 调用配额；返回 True 表示允许调用。

    两道门：
      1) 公司账户池软预留（reserved 状态）— 不要把池子最后几次拼光，让出给其他
         团队。这里查的是已缓存的 account_usage 快照，不打额外网络。
      2) 本项目本地月度上限（独立的硬护栏，防止某个 bug 一夜烧穿）。
    """
    remaining = await _org_remaining_cached()
    if _classify_state(remaining) == "reserved":
        logger.warning(
            "Sensor Tower call refused: org pool reserve guard (remaining=%s ≤ %d). "
            "Serving snapshot/mock instead.",
            remaining, settings.SENSOR_TOWER_ORG_RESERVE,
        )
        return False

    limit = settings.SENSOR_TOWER_MONTHLY_LIMIT
    ym = current_year_month()
    async with AsyncSessionLocal() as session:
        return await _consume_in(session, ym, limit)


async def refund() -> None:
    """退还一次配额：try_consume 已扣但实际请求失败时调用。
    配额是为"成功取到真实数据"付费，失败不该扣——否则一个坏端点能把
    整月预算空烧光。`count > 0` 兜底，并发/误调也不会变负。

    月度 + 当天 daily 同时扣回——refund 在 sensor_tower._cached_get 的 except
    分支里紧跟 try_consume 调用,实际跨 UTC 日的概率可忽略。"""
    ym = current_year_month()
    today = current_date_utc()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE api_quota_monthly SET count = count - 1, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE year_month = :ym AND count > 0"
            ).bindparams(ym=ym)
        )
        await session.execute(
            text(
                "UPDATE api_quota_daily SET count = count - 1, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE date = :d AND count > 0"
            ).bindparams(d=today)
        )
        await session.commit()


async def usage_history(days: int) -> list[dict]:
    """近 N 个 UTC 日的本项目用量(api_quota_daily),缺失日填 0。
    返回升序 [{date, count}],便于前端直接画线图。窗口 N 包含今天。"""
    if days <= 0:
        return []
    from datetime import timedelta

    today = utcnow_naive().date()
    start = today - timedelta(days=days - 1)
    start_str = start.strftime("%Y-%m-%d")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT date, count FROM api_quota_daily "
                "WHERE date >= :start ORDER BY date"
            ).bindparams(start=start_str)
        )
        rows = {r[0]: r[1] for r in result.all()}

    # 显式填零:即使该日无调用也要给一个点,折线才不会跳过"零调用日"
    out: list[dict] = []
    cursor = start
    while cursor <= today:
        d = cursor.strftime("%Y-%m-%d")
        out.append({"date": d, "count": rows.get(d, 0)})
        cursor += timedelta(days=1)
    return out


def _percent(used: int | None, limit: int | None) -> float:
    if not used or not limit or limit <= 0:
        return 0.0
    return round(used / limit * 100, 1)


async def _fetch_account_usage_live() -> Optional[dict]:
    """直连 ST /v1/api_usage 取账户级用量。失败返回 None。

    2026-06-11 实锤：本端点**不计公司池**（连打两次 org.usage 不动，同窗口
    featured/impacts 每次 +1 形成对照），也**不计本地 api_quota_monthly**
    ——故意走裸 httpx 不走 try_consume。TTL 仅用于挡住前端轮询打爆 ST。
    返回外层有 {data: {...}} 包裹（swagger 没标），剥一层再解析。
    """
    if settings.USE_MOCK_DATA or not settings.SENSOR_TOWER_API_KEY:
        return None
    url = f"{settings.SENSOR_TOWER_BASE_URL}/v1/api_usage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url, params={"auth_token": settings.SENSOR_TOWER_API_KEY})
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.error("Failed to fetch ST /v1/api_usage: %s", e)
        return None
    inner = data.get("data", data) if isinstance(data, dict) else {}
    org = inner.get("organization") or {}
    usr = inner.get("user") or {}
    return {
        "organization": {
            "usage": org.get("usage"),
            "limit": org.get("limit"),
            "tier": org.get("tier"),
        },
        "user": {"usage": usr.get("usage")},
    }


async def get_account_usage() -> Optional[dict]:
    """TTL-缓存的 ST 账户级用量。返回 {organization, user, stale} 或 None。

    fresh → 返回缓存（stale=False，不打网络）；
    stale/缺失 → 拉实时，成功落 snapshot；
    实时也失败 → 回退到任何历史快照并标记 stale=True；
    都没有 → 返回 None（前端隐藏 org 行）。
    """
    ttl_seconds = settings.SENSOR_TOWER_ACCOUNT_USAGE_TTL_HOURS * 3600
    fresh = await load_snapshot_if_fresh(ACCOUNT_USAGE_KEY, ttl_seconds)
    if fresh is not None:
        return {**fresh, "stale": False}
    live = await _fetch_account_usage_live()
    if live is not None:
        await save_snapshot(ACCOUNT_USAGE_KEY, live)
        return {**live, "stale": False}
    stale = await load_snapshot(ACCOUNT_USAGE_KEY)
    if stale is not None:
        return {**stale, "stale": True}
    return None


async def current_usage() -> dict:
    """返回 {year_month, used, limit, remaining, percentage, data_source, data_updated_at,
    organization, account_user_usage, account_stale} 供前端展示。"""
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

    # 公司账户级用量（ST 全局共享池），独立的可见性，跟本地 used 不同口径。
    account = await get_account_usage()
    org_block: Optional[dict] = None
    account_user_usage: Optional[int] = None
    account_stale: Optional[bool] = None
    org_remaining: Optional[int] = None
    if account is not None:
        org = account.get("organization") or {}
        org_usage = org.get("usage")
        org_limit = org.get("limit")
        if isinstance(org_usage, int) and isinstance(org_limit, int) and org_limit > 0:
            org_remaining = max(0, org_limit - org_usage)
        org_block = {
            "usage": org_usage,
            "limit": org_limit,
            "remaining": org_remaining,
            "percentage": _percent(org_usage, org_limit),
            "tier": org.get("tier"),
        }
        account_user_usage = (account.get("user") or {}).get("usage")
        account_stale = account.get("stale")

    account_state = _classify_state(org_remaining)

    return {
        "year_month": ym,
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "percentage": round(used / limit * 100, 1) if limit > 0 else 0.0,
        "exhausted": exhausted,
        "data_source": data_source,
        "data_updated_at": data_updated_at,
        "organization": org_block,
        "account_user_usage": account_user_usage,
        "account_stale": account_stale,
        # normal / low / reserved — 前端按此决定是否弹全局警示条。
        # reserved 时本项目 try_consume 已自动停拉，UI 同步告知用户原因。
        "account_state": account_state,
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
