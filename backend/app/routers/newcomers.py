"""/api/newcomers/ —— 新品监测页数据源。

复用 services/newcomers.detect_newcomers（纯检测、无副作用、零 ST 配额），把每个
combo 的"新面孔"打平成扁平列表返回。country+platform 都传则只查那一个组合，否则
跨所有 SYNC_RANKING_COMBOS 汇总。

与 movements 端点同理：纯本地 game_rankings 比对，绝不触发任何 ST 调用或 Sentry 告警。
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, utcnow_naive
from app.models.publisher import PublisherEntity, PublisherItunesArtist, PublisherItunesApp
from app.services.newcomers import (
    detect_newcomers, detect_publisher_newcomers, _load_entity_matchers,
    _load_ignore_keys,
)
from app.services.gp_releases import sync_gp_releases
from app.services.itunes_releases import sync_itunes_releases

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


class PublisherNewcomerItem(BaseModel):
    """已建档厂商主体的一条新品——首次出现在已监测榜单（任意名次）。"""
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
    entity_id: int
    entity_name: str
    matched_by: str  # 'alias' = 发行马甲命中 / 'app_id' = 钉选命中


class PublisherNewcomersOut(BaseModel):
    today: str
    items: list[PublisherNewcomerItem]
    combos_without_baseline: list[str] = []
    as_of_by_combo: dict[str, str] = {}
    window: int


class AppstoreReleaseItem(BaseModel):
    """一条「应用商店新上架」：开发者账号清单 diff 出的新 app（不依赖进榜）。
    platform='gp' 行来自 Google Play 开发者页（track_id=包名，storefronts=['gp']）。"""
    entity_id: int
    entity_name: str
    artist_label: Optional[str] = None
    platform: str = "ios"
    track_id: str
    name: str
    bundle_id: Optional[str] = None
    release_date: Optional[str] = None
    track_view_url: Optional[str] = None
    # 免费 iTunes lookup 同响应里的展示字段（零增量 ST）。
    artwork_url: Optional[str] = None
    genre: Optional[str] = None
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    price: Optional[str] = None
    # 可见区（小写码列表，us 在列 = 美区可见；不在 = 疑似软启动）与检出详情。
    storefronts: list[str] = []
    description: Optional[str] = None
    screenshots: list[str] = []
    first_seen_at: datetime


class AppstoreReleasesOut(BaseModel):
    today: str
    items: list[AppstoreReleaseItem]
    # 已挂账号数 / 已完成首次基线同步的账号数。0 账号或未基线时前端给引导文案。
    artists_total: int = 0
    artists_synced: int = 0
    days: int


@router.get("/appstore", response_model=AppstoreReleasesOut)
async def get_appstore_releases(
    days: int = Query(60, ge=1, le=365, description="只看最近 N 天内首次发现的新上架"),
    db: AsyncSession = Depends(get_db),
):
    """已建档主体开发者账号下的新上架 app（iTunes 清单 diff，免费 API、零 ST 配额）。

    首次同步建立基线（is_baseline=True）不在此列——只报基线之后出现的新 track_id。
    """
    today = utcnow_naive().strftime("%Y-%m-%d")
    since = utcnow_naive() - timedelta(days=days)

    artists = (await db.execute(select(PublisherItunesArtist))).scalars().all()
    rows = (await db.execute(
        select(PublisherItunesApp, PublisherEntity.name,
               PublisherItunesArtist.label, PublisherItunesArtist.platform)
        .join(PublisherEntity, PublisherEntity.id == PublisherItunesApp.entity_id)
        .join(PublisherItunesArtist, PublisherItunesArtist.id == PublisherItunesApp.artist_row_id)
        .where(
            PublisherItunesApp.is_baseline.is_(False),
            PublisherItunesApp.first_seen_at >= since,
        )
        .order_by(PublisherItunesApp.first_seen_at.desc())
    )).all()

    return AppstoreReleasesOut(
        today=today,
        items=[
            AppstoreReleaseItem(
                entity_id=app.entity_id, entity_name=entity_name, artist_label=artist_label,
                platform=platform,
                track_id=app.track_id, name=app.name, bundle_id=app.bundle_id,
                release_date=app.release_date, track_view_url=app.track_view_url,
                artwork_url=app.artwork_url, genre=app.genre, rating=app.rating,
                rating_count=app.rating_count, price=app.price,
                storefronts=[s for s in (app.storefronts or "").split(",") if s],
                description=app.description,
                screenshots=json.loads(app.screenshot_urls) if app.screenshot_urls else [],
                first_seen_at=app.first_seen_at,
            )
            for app, entity_name, artist_label, platform in rows
        ],
        artists_total=len(artists),
        artists_synced=sum(1 for a in artists if a.last_synced_at is not None),
        days=days,
    )


@router.post("/appstore/sync")
async def trigger_appstore_sync():
    """手动触发一轮应用商店清单同步（首次挂账号后建基线用，平时靠定时调度）。
    iOS 走免费 iTunes lookup，GP 走免费开发者页；零 ST 配额；mock 模式空跑。"""
    summary = await sync_itunes_releases()
    gp_summary = await sync_gp_releases()
    return {"message": "ok", **summary, **gp_summary}


@router.get("/publishers", response_model=PublisherNewcomersOut)
async def get_publisher_newcomers(
    window: Optional[int] = Query(None, ge=1, le=20, description="回看多少个同步快照作基线；缺省用 NEWCOMER_WINDOW"),
):
    """已建档厂商主体的新品（跨全部已监测 combo 汇总，**不限名次**）。

    与「全市场新面孔」互补：主体可信，新品在任意名次首次出现都值得提示——
    解决慢慢爬榜的产品被基线"见过"而永不触发 Top N 口径的漏报。零 ST 配额。
    """
    today = utcnow_naive().strftime("%Y-%m-%d")
    eff_window = window if window is not None else settings.NEWCOMER_WINDOW
    matchers = await _load_entity_matchers()

    items: list[PublisherNewcomerItem] = []
    no_baseline: list[str] = []
    as_of_by_combo: dict[str, str] = {}
    for c, p in settings.sync_combos_list:
        summary = await detect_publisher_newcomers(c, p, window=window, matchers=matchers)
        key = f"{c}/{p}"
        if summary["as_of"]:
            as_of_by_combo[key] = summary["as_of"]
        if summary["no_baseline"]:
            no_baseline.append(key)
            continue
        for n in summary["newcomers"]:
            items.append(PublisherNewcomerItem(country=c, platform=p, as_of=summary["as_of"], **n))

    items.sort(key=lambda e: (e.entity_name, e.rank if e.rank is not None else 999))
    return PublisherNewcomersOut(
        today=today,
        items=items,
        combos_without_baseline=no_baseline,
        as_of_by_combo=as_of_by_combo,
        window=eff_window,
    )


class NewcomerHistoryItem(BaseModel):
    """一条已沉淀的新面孔检出（含免费源富化字段，未富化为 NULL）。"""
    id: int
    country: str
    platform: str
    app_id: str
    chart_type: str = "grossing"  # grossing 收入榜 / free 下载榜（ADR 0001）
    as_of: str
    name: str
    publisher: Optional[str] = None
    icon_url: Optional[str] = None
    rank: Optional[int] = None
    revenue: Optional[float] = None
    is_slg: bool
    first_detected_at: datetime
    store_url: Optional[str] = None
    release_date: Optional[str] = None
    genre: Optional[str] = None
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    price: Optional[str] = None
    description: Optional[str] = None
    screenshots: list[str] = []
    enrich_source: Optional[str] = None
    # 读时归属：命中已建档主体（建档后无需回写，历史卡片立刻显示已归属）
    entity_id: Optional[int] = None
    entity_name: Optional[str] = None
    # 检出时是否「回归」（baseline 之外曾出现）。0022 迁移前的历史行为 None ——
    # 前端按真首发处理（向后兼容，缺省 = 老数据照旧显示）。
    is_reentry: Optional[bool] = None


class NewcomerHistoryOut(BaseModel):
    today: str
    items: list[NewcomerHistoryItem]
    days: int
    # 各 combo 锚定的"最近快照日"，前端据此显示「截至 N 天前」新鲜度提示。
    # 与 NewcomersOut 同口径（来自 game_rankings 的 MAX(date) per combo）。
    as_of_by_combo: dict[str, str] = {}


@router.get("/history", response_model=NewcomerHistoryOut)
async def get_newcomer_history(
    days: int = Query(90, ge=1, le=365, description="回看 N 天内的检出"),
    country: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    topn: Optional[int] = Query(None, ge=1, le=200, description="只看名次 ≤ 此值的检出（如 50）"),
    signal: Optional[str] = Query(
        None, pattern="^(true_new|reentry|all)$",
        description=(
            "信号筛选："
            "`true_new`(默认推荐) 仅真首发（is_reentry=False 或 NULL=老数据兼容）；"
            "`reentry` 仅回归（is_reentry=True）；"
            "`all` 全部不筛"
        ),
    ),
    chart: str = Query(
        "grossing", pattern="^(grossing|free|all)$",
        description="榜类型：`grossing`(默认，收入榜)/`free`(下载榜)/`all`(两榜都返回)",
    ),
    db: AsyncSession = Depends(get_db),
):
    """已沉淀的全市场新面孔检出历史（检出即落库 + 免费源富化，零 ST 配额）。

    `signal` 默认不筛（all），交给前端按 is_reentry 字段决定。如果前端想让服务端
    预筛（少传一些行）可显式传 signal=true_new 或 reentry。
    """
    from app.models.newcomer import MarketNewcomerLog
    from sqlalchemy import or_, func as sa_func
    from app.models.game import GameRanking, CHART_GROSSING
    since = utcnow_naive() - timedelta(days=days)
    q = select(MarketNewcomerLog).where(MarketNewcomerLog.first_detected_at >= since)
    if chart != "all":
        q = q.where(MarketNewcomerLog.chart_type == chart)
    if country:
        q = q.where(MarketNewcomerLog.country == country.upper())
    if platform:
        q = q.where(MarketNewcomerLog.platform == platform.lower())
    if topn:
        q = q.where(MarketNewcomerLog.rank <= topn)
    if signal == "true_new":
        # NULL = 老数据未知，按真首发处理（向后兼容，老卡片照旧显示）
        q = q.where(or_(MarketNewcomerLog.is_reentry.is_(False),
                        MarketNewcomerLog.is_reentry.is_(None)))
    elif signal == "reentry":
        q = q.where(MarketNewcomerLog.is_reentry.is_(True))
    rows = (await db.execute(
        q.order_by(MarketNewcomerLog.first_detected_at.desc(), MarketNewcomerLog.rank)
    )).scalars().all()
    # 缺口忽略名单过滤：人工确认的非 SLG 噪声不进沉淀视图（与 /gaps、detect_newcomers
    # 同一名单同口径）。**读时过滤而非删行**——历史日志原样保留，但前端点「忽略」后
    # 该发行商的行立即从视图消失（无需等老化/手动清表）。
    from app.services.newcomers import _load_ignore_keys, _is_ignored
    ignore_pub_keys, ignore_app_ids = await _load_ignore_keys()
    rows = [r for r in rows if not _is_ignored(r.app_id, r.publisher, ignore_pub_keys, ignore_app_ids)]
    from app.services.newcomer_log import attribute_entities
    attributed = await attribute_entities(rows)
    # 数据新鲜度：每 combo 最近一次已同步快照日，让前端给陈旧 combo 加 stale 提示。
    freshness_rows = (await db.execute(
        select(GameRanking.country, GameRanking.platform, sa_func.max(GameRanking.date))
        .where(GameRanking.chart_type == CHART_GROSSING)
        .group_by(GameRanking.country, GameRanking.platform)
    )).all()
    as_of_by_combo = {f"{c}/{p}": d for c, p, d in freshness_rows if d}
    return NewcomerHistoryOut(
        today=utcnow_naive().strftime("%Y-%m-%d"),
        items=[
            NewcomerHistoryItem(
                **{k: getattr(r, k) for k in (
                    "id", "country", "platform", "app_id", "chart_type", "as_of", "name",
                    "publisher", "icon_url", "rank", "revenue", "first_detected_at",
                    "store_url", "release_date", "genre", "rating", "rating_count",
                    "price", "description", "enrich_source", "is_reentry")},
                # 落库后建档的主体读时也算 SLG——is_slg 活算（存档值只作冗余）
                is_slg=r.is_slg or r.id in attributed,
                entity_id=attributed.get(r.id, (None, None))[0],
                entity_name=attributed.get(r.id, (None, None))[1],
                screenshots=json.loads(r.screenshot_urls) if r.screenshot_urls else [],
            )
            for r in rows
        ],
        days=days,
        as_of_by_combo=as_of_by_combo,
    )


@router.post("/history/sync")
async def trigger_newcomer_history_sync():
    """手动触发全 combo 检出落库（首次回填 / 调试用，平时随定时同步自动写）。"""
    from app.services.newcomer_log import record_all_combos
    summary = await record_all_combos()
    return {"message": "ok", **summary}


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

    # 忽略名单循环外预加载一次，避免每 combo 重查 publisher_ignores（跨 combo 一致）。
    ignore_keys = await _load_ignore_keys()
    items: list[NewcomerItem] = []
    no_baseline: list[str] = []
    as_of_by_combo: dict[str, str] = {}
    for c, p in combos:
        summary = await detect_newcomers(c, p, window=window, topn=topn, ignore_keys=ignore_keys)
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
