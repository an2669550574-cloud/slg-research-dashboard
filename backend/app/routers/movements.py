"""今日大事：仪表盘竞品异动可视化数据源。

复用 services/movement.detect_movement（纯检测、无副作用），把每个 SLG 异动
打平成一条结构化事件返回给前端。零 ST 配额，纯本地 game_rankings 比对。

与 Sentry 告警的区别：
- Sentry: `_scheduled_sync` 每日同步后调一次 `detect_and_alert_movement`,
  发一条汇总通知给开发者。是"出事报警"。
- 本端点: 仪表盘每次刷新拉取，给运营/分析人员看，是"日常情报"。
  绝对不能走 alert 路径，否则刷 dashboard = 刷 Sentry。
"""
import logging
from datetime import date as _date
from typing import Literal, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.config import settings
from app.database import utcnow_naive
from app.services.movement import detect_movement

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/movements", tags=["movements"])

MovementKind = Literal["new_entrant", "surge", "drop", "revenue_spike"]


class MovementEvent(BaseModel):
    """一条异动事件——打平了的、前端可直接消费的结构。"""
    kind: MovementKind
    country: str
    platform: str
    today: str
    prev_date: str
    app_id: str
    name: str
    icon_url: Optional[str] = None
    prev_rank: Optional[int] = None
    cur_rank: Optional[int] = None
    prev_revenue: Optional[float] = None
    cur_revenue: Optional[float] = None
    revenue_pct: Optional[float] = None


class MovementsOut(BaseModel):
    today: str
    events: list[MovementEvent]
    # 哪些 combo 没有可比对的历史日（首次同步或冷库）。前端可用此提示"还在累积数据"。
    combos_without_baseline: list[str] = []
    # 哪些 combo 今日 game_rankings 数据缺失或严重不完整(同步失败 / ST 配额耗尽);
    # 前端提示"今日数据缺失",这些 combo 不参与异动判断,避免错报全员"跌出 TOP"。
    combos_with_stale_today: list[str] = []


def _impact(e: MovementEvent) -> tuple:
    """重要性排序键：榜首>榜尾、升幅>跌幅、近期>远期。返回元组用于 sorted。
    数字越小越靠前（sorted 默认升序）。
    """
    # 主键：kind 优先级。new_entrant 进 Top 最吸睛 → drop 次之 → surge → revenue
    kind_pri = {"new_entrant": 0, "drop": 1, "surge": 2, "revenue_spike": 3}[e.kind]
    # 次键：与重要性相关的强度
    if e.kind in ("new_entrant", "surge"):
        # 当前排名越前越重要
        intensity = e.cur_rank or 999
    elif e.kind == "drop":
        # 之前排名越前的跌出影响越大
        intensity = e.prev_rank or 999
    else:  # revenue_spike
        # 收入变化越大越重要（绝对值大优先）
        intensity = -abs(e.revenue_pct or 0)
    return (kind_pri, intensity)


def _flatten(summary: dict) -> list[MovementEvent]:
    """把 detect_movement 返回的分组 dict 打平成事件列表。"""
    common = {
        "country": summary["country"],
        "platform": summary["platform"],
        "today": summary["today"],
        "prev_date": summary["prev_date"] or "",
    }
    out: list[MovementEvent] = []
    for e in summary["new_entrants"]:
        out.append(MovementEvent(kind="new_entrant", **common, **{
            "app_id": e["app_id"], "name": e["name"], "icon_url": e["icon_url"],
            "prev_rank": e["prev_rank"], "cur_rank": e["cur_rank"],
        }))
    for e in summary["surges"]:
        out.append(MovementEvent(kind="surge", **common, **{
            "app_id": e["app_id"], "name": e["name"], "icon_url": e["icon_url"],
            "prev_rank": e["prev_rank"], "cur_rank": e["cur_rank"],
        }))
    for e in summary["drops"]:
        out.append(MovementEvent(kind="drop", **common, **{
            "app_id": e["app_id"], "name": e["name"], "icon_url": e["icon_url"],
            "prev_rank": e["prev_rank"], "cur_rank": e["cur_rank"],
        }))
    for e in summary["revenue_spikes"]:
        out.append(MovementEvent(kind="revenue_spike", **common, **{
            "app_id": e["app_id"], "name": e["name"], "icon_url": e["icon_url"],
            "prev_revenue": e["prev_revenue"], "cur_revenue": e["cur_revenue"],
            "revenue_pct": e["pct"],
        }))
    return out


@router.get("/", response_model=MovementsOut)
async def get_today_movements(
    country: Optional[str] = Query(None, description="国家代码；不传则汇总所有 SYNC_RANKING_COMBOS"),
    platform: Optional[str] = Query(None, description="平台 ios/android；country 不传时本参数也被忽略"),
):
    """今日 SLG 竞品异动。country+platform 都传则只查那一个组合，否则跨所有
    `SYNC_RANKING_COMBOS` 汇总。返回的事件已按重要性排序，前端可直接渲染。"""
    today = utcnow_naive().strftime("%Y-%m-%d")
    if country and platform:
        combos = [(country.upper(), platform.lower())]
    else:
        combos = settings.sync_combos_list

    all_events: list[MovementEvent] = []
    no_baseline: list[str] = []
    stale_today: list[str] = []
    for c, p in combos:
        summary = await detect_movement(c, p, today)
        if summary["prev_date"] is None:
            no_baseline.append(f"{c}/{p}")
            continue
        if summary.get("today_missing"):
            stale_today.append(f"{c}/{p}")
            continue
        all_events.extend(_flatten(summary))

    all_events.sort(key=_impact)
    return MovementsOut(
        today=today,
        events=all_events,
        combos_without_baseline=no_baseline,
        combos_with_stale_today=stale_today,
    )
