from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from typing import Optional, Literal
from app.database import get_db, utcnow_naive
from app.models.game import Game, GameRanking, CHART_GROSSING
from app.rate_limit import refresh_cooldown
from app.services.sensor_tower import sensor_tower_service, MOCK_SLG_GAMES, _resolve_window
from app.services.appstore import fetch_app_info
from app.services.slg_publishers import is_slg
from app.services.sibling_match import find_sibling_app_ids
from app.scheduler import sync_daily_rankings
from app.config import settings
from app.schemas import GameCreate, GameOut, GameUpdate, RankingTodayOut, MetricsOut, MetricsCoverage, AggregateLeaderboardOut

router = APIRouter(prefix="/api/games", tags=["games"])

GAME_SORT_FIELDS = {
    "name": Game.name,
    "publisher": Game.publisher,
    "release_date": Game.release_date,
    "created_at": Game.created_at,
    "updated_at": Game.updated_at,
}


@router.get("/", response_model=list[GameOut])
async def list_games(
    response: Response,
    db: AsyncSession = Depends(get_db),
    platform: Optional[str] = None,
    country: Optional[str] = None,
    publisher: Optional[str] = None,
    q: Optional[str] = Query(None, description="模糊匹配 name 或 publisher"),
    sort_by: Literal["name", "publisher", "release_date", "created_at", "updated_at"] = "name",
    order: Literal["asc", "desc"] = "asc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    base = select(Game)
    if platform:
        base = base.where(Game.platform == platform)
    if country:
        base = base.where(Game.country == country)
    if publisher:
        base = base.where(Game.publisher == publisher)
    if q:
        like = f"%{q}%"
        base = base.where((Game.name.ilike(like)) | (Game.publisher.ilike(like)))

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    response.headers["X-Total-Count"] = str(total)

    sort_col = GAME_SORT_FIELDS[sort_by]
    base = base.order_by(sort_col.desc() if order == "desc" else sort_col.asc())
    base = base.limit(limit).offset(offset)
    result = await db.execute(base)
    return result.scalars().all()


@router.get("/rankings", response_model=list[RankingTodayOut])
async def get_rankings(
    country: str = "US",
    platform: str = "ios",
    db: AsyncSession = Depends(get_db),
):
    """榜单。读 game_rankings 表里该 combo **最近一次已同步**的那天（不强求等于
    今天）——同步降到周级后多数天没有"今日"行，但展示最近一次同步的榜即可，零配额。
    仅当该 combo 库内完全无数据（冷启动）才回退 Sensor Tower（消耗配额）。

    这是配额能真正随同步频率下降的前提：否则非同步日每次打开仪表盘都会因
    "查不到今日行"而回退实时 ST 调用，把省下的配额又漏掉。row.date 带回真实
    同步日期，前端据此显示"数据截至 X"。
    """
    today = utcnow_naive().strftime("%Y-%m-%d")
    latest_date = (await db.execute(
        select(func.max(GameRanking.date)).where(
            GameRanking.country == country,
            GameRanking.platform == platform,
            GameRanking.chart_type == CHART_GROSSING,
            GameRanking.date <= today,
        )
    )).scalar()
    result = await db.execute(
        select(GameRanking)
        .where(
            GameRanking.date == latest_date,
            GameRanking.country == country,
            GameRanking.platform == platform,
            GameRanking.chart_type == CHART_GROSSING,
        )
        .order_by(GameRanking.rank.asc().nulls_last())
    ) if latest_date else None
    rows = result.scalars().all() if result is not None else []
    if rows:
        # 销量周级解耦：非抓取日榜行 dl/rev 为 NULL。为日榜展示不空窗，用该 app
        # 最近一次已知 dl/rev 兜底（仅展示，不回写库——详情页趋势仍读真实 NULL 行，
        # 自然退化成周级数据点，不被污染）。零配额，一次聚合查询。
        carry = await _last_known_sales(
            db, [r.app_id for r in rows if r.downloads is None and r.revenue is None],
            country, platform, today,
        )
        return [
            {
                "app_id": r.app_id,
                "name": r.name or r.app_id,
                "publisher": r.publisher,
                "icon_url": r.icon_url,
                "rank": r.rank,
                "downloads": r.downloads if r.downloads is not None else carry.get(r.app_id, (None, None))[0],
                "revenue": r.revenue if r.revenue is not None else carry.get(r.app_id, (None, None))[1],
                "date": r.date,
                "is_slg": is_slg(r.app_id, r.publisher),
            }
            for r in rows
        ]
    # DB 当日无数据 → 回退 Sensor Tower（可能消耗配额 / 也可能命中 24h snapshot）
    return await sensor_tower_service.get_all_rankings_today(country, platform)


async def _last_known_sales(
    db: AsyncSession, app_ids: list[str], country: str, platform: str, before: str
) -> dict[str, tuple]:
    """给定 app 列表，各取其 < before 日内最近一条有销量(downloads 非 NULL)的
    (downloads, revenue)。零配额、纯本地聚合。空列表直接返回 {}。

    用于日榜读路径 carry-forward：销量周级解耦后非抓取日榜行 dl/rev 为 NULL，
    这里补上"上次已知值"用于展示，不回写库。
    """
    if not app_ids:
        return {}
    latest = (
        select(GameRanking.app_id.label("aid"), func.max(GameRanking.date).label("d"))
        .where(
            GameRanking.app_id.in_(app_ids),
            GameRanking.country == country,
            GameRanking.platform == platform,
            GameRanking.chart_type == CHART_GROSSING,
            GameRanking.date < before,
            GameRanking.downloads.isnot(None),
        )
        .group_by(GameRanking.app_id)
        .subquery()
    )
    res = await db.execute(
        select(GameRanking.app_id, GameRanking.downloads, GameRanking.revenue).join(
            latest,
            and_(GameRanking.app_id == latest.c.aid, GameRanking.date == latest.c.d),
        ).where(GameRanking.country == country, GameRanking.platform == platform,
                GameRanking.chart_type == CHART_GROSSING)
    )
    return {aid: (dl, rv) for aid, dl, rv in res.all()}


@router.post(
    "/rankings/refresh",
    response_model=list[RankingTodayOut],
    dependencies=[Depends(refresh_cooldown)],
)
async def force_refresh_rankings(country: str = "US", platform: str = "ios"):
    """绕过缓存强制重拉今日榜单——dashboard 的"刷新数据"按钮调这里。
    会消耗一次月度配额。服务端 30s cooldown 防止前端 disable 被绕过。

    顺手把新数据同步到 game_rankings 表，让接下来的 /games/rankings (DB 读路径)
    也能看到最新值。sync_daily_rankings 内部再调 get_all_rankings_today 时会命中
    刚刚填的缓存，不会产生第二次 API 调用。
    """
    fresh = await sensor_tower_service.force_refresh_today_rankings(country, platform)
    await sync_daily_rankings(country=country, platform=platform)
    return fresh


@router.post("/sync-rankings")
async def trigger_sync_rankings(country: str = "US", platform: str = "ios"):
    """手动触发一次每日榜单抓取（与定时任务同一逻辑）。"""
    written = await sync_daily_rankings(country=country, platform=platform)
    return {"message": f"已写入 {written} 条排行数据", "country": country, "platform": platform}


@router.get("/seed")
async def seed_games(db: AsyncSession = Depends(get_db)):
    """初始化预置 SLG 游戏数据"""
    for game_data in MOCK_SLG_GAMES:
        exists = await db.execute(select(Game).where(Game.app_id == game_data["app_id"]))
        if exists.scalar_one_or_none():
            continue
        game = Game(**{k: v for k, v in game_data.items() if k in Game.__table__.columns.keys()})
        db.add(game)
    await db.commit()
    return {"message": f"已初始化 {len(MOCK_SLG_GAMES)} 款游戏"}


@router.get("/aggregate-leaderboard", response_model=list[AggregateLeaderboardOut])
async def get_aggregate_leaderboard(
    days: int = Query(30, ge=1, le=365),
    slg_only: bool = True,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """仪表盘「合计·区间」视图数据源：每个 app 在窗口内**跨全部已监测市场**
    按日合计下载/收入，按收入降序。与详情页头部「已监测市场合计」同口径——
    详情页那行的数字与本榜该行的数字应可直接对账（同 days、同 slg 过滤前提下）。

    纯本地聚合，零 ST 配额。`is_slg` 用 game_rankings 行内的 publisher 兜底
    判定（与 /games/rankings 同源）。
    """
    win = _resolve_window(days, None, None)
    res = await db.execute(
        select(
            GameRanking.app_id,
            func.max(GameRanking.name).label("name"),
            func.max(GameRanking.publisher).label("publisher"),
            func.max(GameRanking.icon_url).label("icon_url"),
            func.sum(GameRanking.downloads).label("downloads"),
            func.sum(GameRanking.revenue).label("revenue"),
        ).where(
            GameRanking.chart_type == CHART_GROSSING,
            GameRanking.date >= win[0],
            GameRanking.date <= win[-1],
        ).group_by(GameRanking.app_id)
    )
    items: list[dict] = []
    for r in res.all():
        dl = r.downloads or 0
        rv = r.revenue or 0
        if dl == 0 and rv == 0:
            continue  # rank-only 行（无销量）不进合计榜
        if slg_only and not is_slg(r.app_id, r.publisher):
            continue
        items.append({
            "app_id": r.app_id, "name": r.name, "publisher": r.publisher,
            "icon_url": r.icon_url, "downloads": int(dl), "revenue": float(rv),
        })
    items.sort(key=lambda x: -x["revenue"])
    return items[:limit]


@router.get("/{app_id}", response_model=GameOut)
async def get_game(app_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Game).where(Game.app_id == app_id))
    game = result.scalar_one_or_none()
    if game:
        return game
    # 不是被追踪的 Game（games 表只有 seed + 游戏管理里手动加的），但很可能
    # 是排行榜里的真实竞品。用最近一条 game_rankings 合成最小元信息，让详情页
    # 头部/图表可用——而不是 404 弹窗、头部空白。刻意不写回 games 表：否则
    # 600+ 榜单条目会污染「游戏管理」的人工维护列表。
    r = (await db.execute(
        select(GameRanking).where(GameRanking.app_id == app_id,
                                  GameRanking.chart_type == CHART_GROSSING)
        .order_by(GameRanking.date.desc()).limit(1)
    )).scalar_one_or_none()
    if r:
        now = utcnow_naive()
        return GameOut(
            id=0,  # 0 = 非追踪的合成记录（前端只用 name/publisher/icon_url）
            app_id=r.app_id,
            name=r.name or r.app_id,
            publisher=r.publisher,
            icon_url=r.icon_url,
            platform=r.platform,
            country=r.country,
            created_at=now,
            updated_at=now,
        )
    raise HTTPException(status_code=404, detail="Game not found")


@router.post("/lookup")
async def lookup_app(app_id: str, country: str = "us"):
    """通过 iTunes Search API 查询 App 元信息（名称/发行商/图标/发布日期等）。

    用于前端"创建游戏"前预览，前端可点击"使用此结果"再 POST 到 /api/games/。
    """
    info = await fetch_app_info(app_id, country=country)
    if not info:
        raise HTTPException(status_code=404, detail="App not found in iTunes")
    return info


@router.post("/", response_model=GameOut, status_code=201)
async def create_game(data: GameCreate, db: AsyncSession = Depends(get_db)):
    exists = await db.execute(select(Game).where(Game.app_id == data.app_id))
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Game already exists")

    payload = data.model_dump()
    # 若关键字段缺失，尝试用 iTunes 自动补全（ios + 数字 app_id 才会成功）
    needs_lookup = not payload.get("name") or not payload.get("publisher") or not payload.get("icon_url")
    if needs_lookup and payload.get("platform", "ios") == "ios":
        info = await fetch_app_info(payload["app_id"])
        if info:
            for key in ("name", "publisher", "icon_url", "release_date", "description"):
                if not payload.get(key) and info.get(key):
                    payload[key] = info[key]

    if not payload.get("name"):
        raise HTTPException(status_code=400, detail="name is required (iTunes auto-fill failed)")

    game = Game(**payload)
    db.add(game)
    await db.commit()
    await db.refresh(game)
    return game


@router.put("/{app_id}", response_model=GameOut)
async def update_game(app_id: str, data: GameUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Game).where(Game.app_id == app_id))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(game, k, v)
    await db.commit()
    await db.refresh(game)
    return game


@router.delete("/{app_id}")
async def delete_game(app_id: str, db: AsyncSession = Depends(get_db)):
    """删除游戏记录。关联的 rankings/history/materials 不级联删除（保留历史）。"""
    result = await db.execute(select(Game).where(Game.app_id == app_id))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    await db.delete(game)
    await db.commit()
    return {"message": "deleted", "app_id": app_id}


@router.get("/{app_id}/coverage", response_model=list[MetricsCoverage])
async def get_game_metrics_coverage(
    app_id: str,
    merge_siblings: bool = Query(False, description="跨同款 iOS/Android 姐妹 app_id 合并市场列表"),
    db: AsyncSession = Depends(get_db),
):
    """该 app 在本地 game_rankings 里实际有数据的 (国家,平台) 组合。

    详情页图表曾死写 US/ios，但库里只按 SYNC_RANKING_COMBOS 累积，多数 app
    其实只在 US/android 或 JP/KR/ios 进榜 → US/ios 查空、ST 回退也空 → 三图空白。
    前端拿这个列表渲染国家/平台切换，默认选销量覆盖最全的组合。**零 ST 配额**
    （纯本地聚合，与发展历程同源）。返回按"销量天数多→少、再按 SYNC_RANKING_COMBOS
    偏好序"排，故 items[0] 即最佳默认。

    `merge_siblings=true`：同款游戏的 iOS app_id 与 Android app_id 在表里独立累积；
    开启后把姐妹 app_id 的覆盖一并算上，让详情页 chip 一次性看到 iOS+Android 所有
    市场。识别规则见 [[sibling_match]]。
    """
    app_ids = await find_sibling_app_ids(db, app_id) if merge_siblings else [app_id]
    res = await db.execute(
        select(
            GameRanking.country,
            GameRanking.platform,
            func.count().label("days"),
            func.count(GameRanking.revenue).label("sales_days"),
            func.count(GameRanking.rank).label("rank_days"),
        )
        .where(GameRanking.app_id.in_(app_ids),
               GameRanking.chart_type == CHART_GROSSING)
        .group_by(GameRanking.country, GameRanking.platform)
    )
    pref = {cp: i for i, cp in enumerate(settings.sync_combos_list)}
    items = [
        MetricsCoverage(country=c, platform=p, days=d, sales_days=s, rank_days=rk)
        for c, p, d, s, rk in res.all()
    ]
    items.sort(key=lambda x: (-x.sales_days, pref.get((x.country, x.platform), 99)))
    return items


@router.get("/{app_id}/metrics", response_model=MetricsOut)
async def get_game_metrics(
    app_id: str,
    days: int = 30,
    country: str = "WW",
    platform: str = "ios",
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD；与 end_date 同时提供时优先于 days"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    aggregate: bool = Query(False, description="跨该 app 全部已监测市场按日合计下载/收入"),
    merge_siblings: bool = Query(False, description="跨同款 iOS/Android 姐妹 app_id 合并数据"),
    db: AsyncSession = Depends(get_db),
):
    # 与 coverage 同：开启 merge_siblings 则把姐妹 app_id 视为同款一起算。
    # 单市场 (country,platform) 路径：sibling 列表里通常只有 1 个 app_id 真有该
    # 组合的数据，IN 查询自然只命中那一个；不会因为 sibling 制造重复行。
    app_ids = await find_sibling_app_ids(db, app_id) if merge_siblings else [app_id]

    if aggregate:
        # 单产品总计：把该 app 在 game_rankings 里所有 (国家,平台) 行按日
        # 求和 —— 纯本地、零 ST 配额、与发展历程同源。(app_id,date,country,
        # platform) 唯一约束保证不重复计。rank 不能跨市场相加（#1+#5≠#6），
        # 故 rankings 留空，前端在合计视图提示"排名按市场单看"。
        win = _resolve_window(days, start_date, end_date)
        res = await db.execute(
            select(
                GameRanking.date,
                func.sum(GameRanking.downloads),
                func.sum(GameRanking.revenue),
            ).where(
                GameRanking.app_id.in_(app_ids),
                GameRanking.chart_type == CHART_GROSSING,
                GameRanking.date >= win[0],
                GameRanking.date <= win[-1],
            ).group_by(GameRanking.date).order_by(GameRanking.date)
        )
        rows = res.all()
        downloads = [{"date": d, "value": dl} for d, dl, _ in rows if dl is not None]
        revenue = [{"date": d, "value": rv} for d, _, rv in rows if rv is not None]
        return {"rankings": [], "downloads": downloads, "revenue": revenue}

    kw = {"country": country, "platform": platform, "days": days, "start_date": start_date, "end_date": end_date}
    if sensor_tower_service.use_mock:
        sales = await sensor_tower_service.get_sales(app_id, **kw)
        rankings = await sensor_tower_service.get_rankings(app_id, **kw)
        return {"rankings": rankings, "downloads": sales["downloads"], "revenue": sales["revenue"]}

    # 真实模式：rank + 下载 + 收入 一次性读本地 game_rankings（调度采集 + 一次性
    # 历史回填）—— 零 ST 配额、瞬开、与发展历程同源。rank=NULL 的回填行不进
    # 排名走势。库里完全没有销量覆盖（非 Top50 回填 / 未同步市场）时才回退 ST
    # 取下载收入，避免图表空白；行为对未覆盖 app 与改动前一致。
    win = _resolve_window(days, start_date, end_date)
    res = await db.execute(
        select(GameRanking.date, GameRanking.rank, GameRanking.downloads, GameRanking.revenue).where(
            GameRanking.app_id.in_(app_ids),
            GameRanking.country == country,
            GameRanking.platform == platform,
            GameRanking.chart_type == CHART_GROSSING,
            GameRanking.date >= win[0],
            GameRanking.date <= win[-1],
        ).order_by(GameRanking.date)
    )
    rows = res.all()
    rankings = [{"date": d, "rank": rk} for d, rk, _, _ in rows if rk is not None]
    downloads = [{"date": d, "value": dl} for d, _, dl, _ in rows if dl is not None]
    revenue = [{"date": d, "value": rv} for d, _, _, rv in rows if rv is not None]
    if not downloads and not revenue:
        sales = await sensor_tower_service.get_sales(app_id, **kw)
        downloads, revenue = sales["downloads"], sales["revenue"]
    return {"rankings": rankings, "downloads": downloads, "revenue": revenue}
