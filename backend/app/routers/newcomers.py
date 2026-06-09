"""/api/newcomers/ —— 新品监测页数据源。

复用 services/newcomers.detect_newcomers（纯检测、无副作用、零 ST 配额），把每个
combo 的"新面孔"打平成扁平列表返回。country+platform 都传则只查那一个组合，否则
跨所有 SYNC_RANKING_COMBOS 汇总。

与 movements 端点同理：纯本地 game_rankings 比对，绝不触发任何 ST 调用或 Sentry 告警。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.config import settings
from app.database import utcnow_naive
from app.services.newcomers import detect_newcomers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/newcomers", tags=["newcomers"])


class NewcomerItem(BaseModel):
    """一条"新面孔"——打平了的、前端可直接消费的结构。"""
    country: str
    platform: str
    as_of: str
    app_id: str
    name: str
    publisher: Optional[str] = None
    icon_url: Optional[str] = None
    rank: Optional[int] = None
    revenue: Optional[float] = None
    downloads: Optional[float] = None
    # 发行商命中 SLG 白名单。仅用于前端区分"已识别 SLG"vs"新厂商待识别"，不参与过滤。
    is_slg: bool = False


class NewcomersOut(BaseModel):
    today: str
    items: list[NewcomerItem]
    # 该 combo 缺历史快照(冷库/首次同步)——无从判断"新面孔"，前端可提示"还在积累数据"。
    combos_without_baseline: list[str] = []
    # 各 combo 锚定的"最近快照日"。前端据此显示"数据截至 X"。
    as_of_by_combo: dict[str, str] = {}
    # 当次生效的判定口径（窗口 / 名次门槛），前端展示给用户看清"新"的定义。
    window: int
    topn: int


@router.get("/", response_model=NewcomersOut)
async def get_newcomers(
    country: Optional[str] = Query(None, description="国家代码；不传则汇总所有 SYNC_RANKING_COMBOS"),
    platform: Optional[str] = Query(None, description="平台 ios/android；country 不传时本参数也被忽略"),
    window: Optional[int] = Query(None, ge=1, le=20, description="回看多少个同步快照作基线；缺省用 NEWCOMER_WINDOW"),
    topn: Optional[int] = Query(None, ge=1, le=200, description="名次 ≤ 此值才算新进榜；缺省用 NEWCOMER_TOPN"),
):
    """近期首次进榜的新面孔。已按名次升序，前端可直接渲染。"""
    today = utcnow_naive().strftime("%Y-%m-%d")
    eff_window = window if window is not None else settings.NEWCOMER_WINDOW
    eff_topn = topn if topn is not None else settings.NEWCOMER_TOPN

    if country and platform:
        combos = [(country.upper(), platform.lower())]
    else:
        combos = settings.sync_combos_list

    items: list[NewcomerItem] = []
    no_baseline: list[str] = []
    as_of_by_combo: dict[str, str] = {}
    for c, p in combos:
        summary = await detect_newcomers(c, p, window=window, topn=topn)
        key = f"{c}/{p}"
        if summary["as_of"]:
            as_of_by_combo[key] = summary["as_of"]
        if summary["no_baseline"]:
            no_baseline.append(key)
            continue
        for n in summary["newcomers"]:
            items.append(NewcomerItem(country=c, platform=p, as_of=summary["as_of"], **n))

    # 名次靠前优先(rank 缺失兜底沉底)。同 combo 内 detect 已按名次序，跨 combo 再统一排。
    items.sort(key=lambda e: e.rank if e.rank is not None else 999)
    return NewcomersOut(
        today=today,
        items=items,
        combos_without_baseline=no_baseline,
        as_of_by_combo=as_of_by_combo,
        window=eff_window,
        topn=eff_topn,
    )
