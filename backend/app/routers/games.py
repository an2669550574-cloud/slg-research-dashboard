from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional, Literal
from app.database import get_db
from app.models.game import Game
from app.services.sensor_tower import sensor_tower_service, MOCK_SLG_GAMES
from app.services.appstore import fetch_app_info
from app.scheduler import sync_daily_rankings
from app.schemas import GameCreate, GameOut, GameUpdate, RankingTodayOut, MetricsOut

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
async def get_rankings(country: str = "US", platform: str = "ios"):
    return await sensor_tower_service.get_all_rankings_today(country, platform)


@router.post("/rankings/refresh", response_model=list[RankingTodayOut])
async def force_refresh_rankings(country: str = "US", platform: str = "ios"):
    """绕过缓存强制重拉今日榜单——dashboard 的"刷新数据"按钮调这里。
    会消耗一次月度配额。"""
    return await sensor_tower_service.force_refresh_today_rankings(country, platform)


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


@router.get("/{app_id}", response_model=GameOut)
async def get_game(app_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Game).where(Game.app_id == app_id))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return game


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


@router.get("/{app_id}/metrics", response_model=MetricsOut)
async def get_game_metrics(
    app_id: str,
    days: int = 30,
    country: str = "WW",
    platform: str = "ios",
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD；与 end_date 同时提供时优先于 days"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
):
    kw = {"country": country, "platform": platform, "days": days, "start_date": start_date, "end_date": end_date}
    rankings = await sensor_tower_service.get_rankings(app_id, **kw)
    downloads = await sensor_tower_service.get_downloads(app_id, **kw)
    revenue = await sensor_tower_service.get_revenue(app_id, **kw)
    return {"rankings": rankings, "downloads": downloads, "revenue": revenue}
