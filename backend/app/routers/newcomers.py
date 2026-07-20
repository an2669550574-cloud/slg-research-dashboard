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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, utcnow_naive
from app.models.newcomer import NewcomerVideo
from app.models.publisher import PublisherEntity, PublisherItunesArtist, PublisherItunesApp
from app.services.newcomers import (
    detect_newcomers, detect_publisher_newcomers, _load_entity_matchers,
    _load_ignore_keys, gate_publisher_newcomers_by_release_date,
    compute_trajectories,
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
    # 真实上架日（免费富化，零 ST）。判「新」的依据：本地榜单"首次出现"只是上线日的
    # 代理，故再按真实上架日门控（早于 ITUNES_RELEASES_OLD_RELEASE_DAYS 已剔除）。
    # 缺失（免费源未命中）= None，前端按"无从判断"显示，不剔除。
    release_date: Optional[str] = None
    # baseline 窗口之外更早出现过 = 回归（老游戏跌出又回来），非真首发。None=无从判断。
    is_reentry: Optional[bool] = None


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
    # P1-1：软启动新品若已写影子行并中文化，带一句话摘要（track_id ≡ market_newcomer_log.app_id）。
    summary_cn: Optional[str] = None


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
    # P1-1：这些雷达新上架若已写影子行并中文化，带上 📝 摘要（track_id ≡ app_id）。
    from app.models.newcomer import MarketNewcomerLog
    track_ids = [app.track_id for app, *_ in rows if app.track_id]
    summaries: dict[str, str] = {}
    if track_ids:
        for aid, sc in (await db.execute(
            select(MarketNewcomerLog.app_id, MarketNewcomerLog.summary_cn).where(
                MarketNewcomerLog.app_id.in_(track_ids),
                MarketNewcomerLog.summary_cn.is_not(None))
        )).all():
            summaries.setdefault(aid, sc)

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
                summary_cn=summaries.get(app.track_id),
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
        # 真实上架日门控：剔除老产品（本地"首次出现"≠ 真新品）+ 回填 release_date。
        # 缓存优先（检出历史 / 雷达 app），miss 才打免费 lookup，零 ST 配额。
        gated = await gate_publisher_newcomers_by_release_date(summary["newcomers"], c, p)
        for n in gated:
            items.append(PublisherNewcomerItem(country=c, platform=p, as_of=summary["as_of"], **n))

    items.sort(key=lambda e: (e.entity_name, e.rank if e.rank is not None else 999))
    return PublisherNewcomersOut(
        today=today,
        items=items,
        combos_without_baseline=no_baseline,
        as_of_by_combo=as_of_by_combo,
        window=eff_window,
    )


class NewcomerTrajectory(BaseModel):
    """检出后走势（读时算 game_rankings，零 ST）。见 services.newcomers.compute_trajectories。

    把「新品检出即阅后即焚」补成态势：这批新品现在是起飞、平稳、下滑还是已掉榜。
    """
    current_rank: Optional[int] = None    # 最新快照名次（掉出采集深度 → None）
    current_as_of: Optional[str] = None   # 最新快照日
    peak_rank: Optional[int] = None       # 检出以来最好（最小）名次
    last_seen: Optional[str] = None       # 最近一次仍在榜的快照日
    days_tracked: Optional[int] = None    # 检出日 → 最新快照的日历跨度
    on_chart: bool = False                # 最新快照里是否还在
    trend: str = "unknown"                # climbing/falling/stable/dropped/new/unknown


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
    # 中文化（LLM 网关，只对 is_slg 新品）：一句话摘要 + 描述全文中译；未翻为 NULL。
    summary_cn: Optional[str] = None
    description_cn: Optional[str] = None
    # 玩法子品类（LLM 受控词表，按核心机制非题材：数字门SLG/基地建设SLG/塔防/…）。
    # 已花 LLM 算出并驱动 ⚔️ 同赛道/赛道脉搏，这里透出给卡片/抽屉直读。未分类为 NULL。
    subgenre_cn: Optional[str] = None
    screenshots: list[str] = []
    # 版本号 / 版本更新日 / 支持语言（iTunes 富化有，GP 留 NULL）。切片 3.1。
    version: Optional[str] = None
    current_version_date: Optional[str] = None
    languages: Optional[str] = None
    enrich_source: Optional[str] = None
    # 读时归属：命中已建档主体（建档后无需回写，历史卡片立刻显示已归属）
    entity_id: Optional[int] = None
    entity_name: Optional[str] = None
    # 检出时是否「回归」（baseline 之外曾出现）。0022 迁移前的历史行为 None ——
    # 前端按真首发处理（向后兼容，缺省 = 老数据照旧显示）。
    is_reentry: Optional[bool] = None
    # 是否已晋升 tracked（games 表读时活算）：卡面晋升按钮据此显隐、抽屉按钮转「已追踪」态。
    is_tracked: bool = False
    # 检出后走势（读时算 game_rankings，零 ST）。无后续快照点时字段全空、trend='unknown'。
    trajectory: Optional[NewcomerTrajectory] = None


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
    from app.services.newcomer_log import CHART_RADAR
    from app.services.rss_earlybird import CHART_RSS
    since = utcnow_naive() - timedelta(days=days)
    q = select(MarketNewcomerLog).where(MarketNewcomerLog.first_detected_at >= since)
    # 影子行不进市场卡片网格（含 chart=all）：radar（P1-1）与 rss（ADR 0005）都只为
    # riding 富化/翻译/视频管道 + 各自分发面（商店雷达区块 / 维护者卡「⚡ RSS 早鸟」段）。
    q = q.where(MarketNewcomerLog.chart_type.notin_((CHART_RADAR, CHART_RSS)))
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
    from app.services.newcomer_log import attribute_entities, slg_app_ids_known
    attributed = await attribute_entities(rows)
    # is_slg 按 app_id 聚合活算（而非逐行）：同一游戏跨 combo 判定分裂（本地化
    # publisher 串 miss）时，任一行为 1 / 任一行归属到 **is_slg 主体** / log 记忆
    # （响应窗口外的行，如另一榜、更早检出）曾判 1 → 该 app 全部行算 SLG。
    # 归属到 is_slg=False 档案（调研/资本系）只展示 entity_name、不当 SLG 信号。
    slg_app_ids = ({r.app_id for r in rows if r.is_slg}
                   | {r.app_id for r in rows
                      if r.id in attributed and attributed[r.id][2]})
    slg_app_ids |= await slg_app_ids_known(
        {r.app_id for r in rows} - slg_app_ids)
    # 已晋升 tracked 的 app（games 表读时活算）：卡面/抽屉晋升入口据此显隐。
    from app.models.game import Game
    tracked_ids = set((await db.execute(select(Game.app_id))).scalars().all())
    # 检出后走势：每行 join game_rankings 算「现在名次到哪了/是否掉榜」（零 ST）。
    trajectories = await compute_trajectories(rows)
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
                    "price", "description", "summary_cn", "description_cn", "subgenre_cn",
                    "version", "current_version_date",
                    "languages", "enrich_source", "is_reentry")},
                # 落库后建档的主体读时也算 SLG——is_slg 按 app_id 聚合活算（存档值只作冗余）
                is_slg=r.app_id in slg_app_ids,
                is_tracked=r.app_id in tracked_ids,
                entity_id=attributed.get(r.id, (None, None, False))[0],
                entity_name=attributed.get(r.id, (None, None, False))[1],
                screenshots=json.loads(r.screenshot_urls) if r.screenshot_urls else [],
                trajectory=(NewcomerTrajectory(**trajectories[r.id])
                            if r.id in trajectories else None),
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


class SubgenrePulseBucket(BaseModel):
    """一个玩法子品类的近窗口新品热度 + 环比。"""
    subgenre: str
    count: int          # 当前窗口去重后落此子品类的新品数
    prev_count: int     # 上一个等长窗口
    delta: int          # count - prev_count（>0 升温 / <0 降温）


class SubgenrePulseOut(BaseModel):
    today: str
    days: int
    total: int                          # 当前窗口有子品类分类的新品总数（去重）
    buckets: list[SubgenrePulseBucket]  # 按当前窗口新品数降序


class SubgenreOverrideIn(BaseModel):
    """人工判定的玩法子品类。subgenre_cn=None = 人工判定「无合适子品类」（同样锁定）。"""
    app_id: str
    subgenre_cn: Optional[str] = None
    name: Optional[str] = None


@router.post("/subgenre-override", status_code=204)
async def override_subgenre(data: SubgenreOverrideIn):
    """人工覆盖某 app 的玩法子品类，之后 LLM 不再改动它。

    深度溯源得出的结论此前撑不过下一次同 app 新检出：LLM 分类挂在 market_newcomer_log 的
    **行**上，而新检出行（summary_cn 为空）会触发重译、并按 app_id 回写该 app 全部行——
    2026-07-20 实测把前一天人工改好的 Battle Kiss 子品类冲掉了。本端点把判定写进
    app_subgenre 并标 source='manual'，那是 LLM 管道碰不到的地方（回补 drain 把「已在本表
    的 app」整体排除在候选外），读取侧 resolve_subgenres 又给它最高优先级。

    只收受控词表内的值（同 LLM 写回口径），防止拍脑袋造词让精确匹配永远命不中。
    **必须先于 GET /{...} 动态段声明**（本路由的字面量段惯例，见 /subgenre-pulse）。
    """
    from app.services.app_subgenre import set_manual_subgenre
    from app.services.newcomer_i18n import SUBGENRE_VOCAB
    if data.subgenre_cn is not None and data.subgenre_cn not in SUBGENRE_VOCAB:
        raise HTTPException(422, f"子品类须为受控词表内的值：{'/'.join(SUBGENRE_VOCAB)}")
    await set_manual_subgenre(data.app_id, data.subgenre_cn, name=data.name)


@router.get("/subgenre-pulse", response_model=SubgenrePulseOut)
async def get_subgenre_pulse(
    days: int = Query(30, ge=7, le=180, description="回看窗口（天）；环比对上一个等长窗口"),
):
    """赛道脉搏：近 days 天检出的新品按玩法子品类（`subgenre_cn`）分布 + 环比（P1-2 stretch）。

    回答「哪个赛道最近在冒新品 / 升温降温」——数字门 SLG 整体在热还是冷。零 ST。
    按 app_id 去重（同 app 跨 combo/chart 多行算一个新品），用该 app **最早检出**
    (`min(first_detected_at)`) 定位落哪个窗口。忽略名单过滤（与 /history 同口径）。
    radar 影子行也计入（软启动新品同样是赛道信号）。计算逻辑抽到
    `services.newcomers.compute_subgenre_pulse`，与月度 rollup 赛道段共用、口径永不漂移。
    """
    from app.services.newcomers import compute_subgenre_pulse
    total, buckets = await compute_subgenre_pulse(days)
    return SubgenrePulseOut(
        today=utcnow_naive().strftime("%Y-%m-%d"), days=days, total=total,
        buckets=[SubgenrePulseBucket(**b) for b in buckets],
    )


class StoreDetailOut(BaseModel):
    """单个 app 的商店详情（按需实时富化，零落库、零 ST 配额）。

    给「厂商新品」等不落库视图用：检出沉淀(MarketNewcomerLog)是落库时富化，
    但厂商新品实时检测、任意名次、多不在 Top100，没有沉淀富化可读——故点开
    详情时对该 app_id 现打一次免费 iTunes lookup / GP 页解析（与落库富化同源
    enrich_fields），found=False 表示免费源未命中（区域限定 / 已下架）。"""
    app_id: str
    platform: str
    found: bool
    enrich_source: Optional[str] = None
    store_url: Optional[str] = None
    release_date: Optional[str] = None
    genre: Optional[str] = None
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    price: Optional[str] = None
    description: Optional[str] = None
    screenshots: list[str] = []
    version: Optional[str] = None
    current_version_date: Optional[str] = None
    languages: Optional[str] = None


@router.get("/enrich", response_model=StoreDetailOut)
async def enrich_app_detail(
    app_id: str = Query(..., description="iOS 数字 trackId / Android GP 包名"),
    platform: str = Query("ios", pattern="^(ios|android)$"),
    country: str = Query("us", description="iOS 优先查的 storefront（miss 退避 us/sg）"),
):
    """按需取单个 app 的商店详情——免费源（iOS=iTunes lookup / Android=GP 页），零 ST。

    不落库（实时）：厂商新品视图点开详情时调，任意 app_id 即时可见版本/语言/简介/截图。
    富化失败返回 found=False（前端降级提示），不抛错——与落库富化同哲学。
    """
    from app.services.newcomer_log import enrich_fields
    data = await enrich_fields(app_id, country.lower(), platform.lower())
    if not data:
        return StoreDetailOut(app_id=app_id, platform=platform, found=False)
    shots = json.loads(data["screenshot_urls"]) if data.get("screenshot_urls") else []
    return StoreDetailOut(
        app_id=app_id, platform=platform, found=True, screenshots=shots,
        **{k: data.get(k) for k in (
            "enrich_source", "store_url", "release_date", "genre", "rating",
            "rating_count", "price", "description", "version",
            "current_version_date", "languages")},
    )


class NewcomerVideoOut(BaseModel):
    """一条实机玩法视频候选（newcomer_video 表，ADR 0002 切片 1c）。"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    app_id: str
    video_id: str
    title: str
    channel: Optional[str] = None
    thumbnail: Optional[str] = None
    url: str
    published_at: Optional[str] = None
    rank: Optional[int] = None
    hidden_at: Optional[datetime] = None  # 软删去噪标记；默认列表里恒为 None


@router.get("/videos", response_model=list[NewcomerVideoOut])
async def get_newcomer_videos(
    app_id: str = Query(..., description="iOS 数字 trackId / Android GP 包名"),
    include_hidden: bool = Query(
        False, description="True 时连人工去噪(软删)的候选一并返回，供回溯统计召回噪声率"),
    db: AsyncSession = Depends(get_db),
):
    """某 app 的实机玩法视频候选（定时自动搜来、零 ST）。按候选序升序。

    人工去噪走软删：默认只返回未隐藏候选（`hidden_at IS NULL`），与前端 UX 一致；
    传 include_hidden=true 可连被删的噪声样本一起取出（观察召回质量 / 设计停用词）。
    """
    stmt = select(NewcomerVideo).where(NewcomerVideo.app_id == app_id)
    if not include_hidden:
        stmt = stmt.where(NewcomerVideo.hidden_at.is_(None))
    rows = (await db.execute(
        # rank 缺失沉底（SQLite NULL 默认排最前）；正常都有值，防御性写法与其它端点一致。
        stmt.order_by(NewcomerVideo.rank.is_(None), NewcomerVideo.rank, NewcomerVideo.id)
    )).scalars().all()
    return rows


@router.delete("/videos/{vid}")
async def delete_newcomer_video(vid: int, db: AsyncSession = Depends(get_db)):
    """人工去噪：软删一条不相关候选（置 hidden_at，保留行）。

    软删而非物删：保留噪声样本，供回溯统计召回噪声率 + 设计停用词（ADR 0002 观察缺口）。
    默认列表 (`hidden_at IS NULL`) 不再返回它，前端 UX 与硬删一致；已记搜索台账，
    不会被重新搜回。幂等：重复删不刷新 hidden_at。
    """
    row = await db.get(NewcomerVideo, vid)
    if row is not None and row.hidden_at is None:
        row.hidden_at = utcnow_naive()
        await db.commit()
    return {"message": "ok"}


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
