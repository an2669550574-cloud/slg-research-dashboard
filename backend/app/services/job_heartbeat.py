"""定时 job 心跳：记录关键 job「上次成功完成」+ 自检超期（补静默失败盲区，A3 前科）。

各 job 的 try/except 只 catch **崩溃**（→ Sentry）；job 若停止被调度（禁用 / scheduler 没起来）
既不崩也不产出，静默无人知。关键 job 成功完成后 `record_heartbeat`；每日 digest 尾部
`get_stale_jobs` 自检——**有过成功记录却超期** → 维护者卡 ⚠️（从没记录的 job 不误报）。

与平淡日「心跳卡」(DIGEST_HEARTBEAT_ENABLED，keep-alive 空卡) 是两回事，勿混。
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.database import AsyncSessionLocal, utcnow_naive
from app.models.digest import JobHeartbeat

logger = logging.getLogger(__name__)

# 关键 job → 预期最长「上次成功」年龄（小时）；超过即视为可能静默停摆。
# 值 = 预期节奏 * 宽限（日更给 2 天、周更给 ~10 天、月更给 ~38 天），容忍偶发漏跑 / misfire。
HEARTBEAT_MAX_AGE_HOURS: dict[str, int] = {
    "daily_rankings_sync": 48,       # US/iOS 每日同步
    "itunes_releases_sync": 48,      # 商店雷达每日
    "newcomer_video_sync": 72,       # 新品视频每日（召回稀疏，给 3 天）
    "region_launch_sync": 240,       # 分地区上线 周级
    "weekly_newcomer_review": 240,   # 新品周察 周级
    "monthly_market_rollup": 912,    # 月度市场月报 月级（~38 天）
}

# 维护者卡展示用中文名。
HEARTBEAT_LABELS: dict[str, str] = {
    "daily_rankings_sync": "榜单同步",
    "itunes_releases_sync": "商店雷达",
    "newcomer_video_sync": "新品视频搜集",
    "region_launch_sync": "分地区上线刷新",
    "weekly_newcomer_review": "新品周察",
    "monthly_market_rollup": "月度市场月报",
}


async def record_heartbeat(job_name: str, note: Optional[str] = None) -> None:
    """job 成功完成后 upsert last_ok_at=now。**失败静默**（心跳记账绝不能拖垮 job 本身）。"""
    try:
        async with AsyncSessionLocal() as db:
            row = await db.get(JobHeartbeat, job_name)
            if row is None:
                db.add(JobHeartbeat(job_name=job_name, last_ok_at=utcnow_naive(), note=note))
            else:
                row.last_ok_at = utcnow_naive()
                row.note = note
            await db.commit()
    except Exception:
        logger.warning("record_heartbeat failed for %s (non-fatal)", job_name, exc_info=True)


async def get_stale_jobs(now: Optional[datetime] = None) -> list[dict]:
    """返回「有过成功记录却已超期」的 job（按超期倍数降序）。

    [{job_name, label, last_ok_at, age_hours, max_age_hours}]。从没记录的 job（新加 / 没跑过）
    不算——只报「本来在跑、悄悄停了」的真实回归。未在 HEARTBEAT_MAX_AGE_HOURS 的 job 忽略。
    """
    now = now or utcnow_naive()
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(JobHeartbeat))).scalars().all()
    stale: list[dict] = []
    for r in rows:
        max_age = HEARTBEAT_MAX_AGE_HOURS.get(r.job_name)
        if max_age is None:
            continue
        age_h = (now - r.last_ok_at).total_seconds() / 3600
        if age_h > max_age:
            stale.append({
                "job_name": r.job_name,
                "label": HEARTBEAT_LABELS.get(r.job_name, r.job_name),
                "last_ok_at": r.last_ok_at,
                "age_hours": age_h,
                "max_age_hours": max_age,
            })
    stale.sort(key=lambda x: x["age_hours"] / x["max_age_hours"], reverse=True)
    return stale


def render_stale_alert(stale: list[dict]) -> Optional[str]:
    """把超期 job 列表渲染成 ⚠️ 段（markdown 引用块，无前导分隔）；空 → None。

    调用方按场景加壳：附在维护者卡尾时前置 `\\n\\n---\\n\\n`；单独成卡时前置标题。
    """
    if not stale:
        return None
    lines = ["> ⚠️ **任务自检**：以下定时任务疑似静默停摆（有成功记录但已超期）"]
    for s in stale:
        days = s["age_hours"] / 24
        lines.append(f"> - **{s['label']}**（`{s['job_name']}`）：上次成功 "
                     f"{s['last_ok_at'].strftime('%m-%d %H:%M')} UTC，已 **{days:.1f} 天**没跑成功")
    return "\n".join(lines)
