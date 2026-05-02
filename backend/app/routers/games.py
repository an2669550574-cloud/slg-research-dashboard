from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models.game import Game, GameRanking
from app.services.sensor_tower import sensor_tower_service, MOCK_SLG_GAMES
from app.services.appstore import fetch_app_info

router = APIRouter(prefix="/api/games", tags=["games"])

class GameCreate(BaseModel):
    app_id: str
    name: str
    publisher: Optional[str] = None
    icon_url: Optional[str] = None
    platform: str = "ios"
    country: str = "US"
    release_date: Optional[str] = None
    description: Optional[str] = None
    tags: list[str] = []

@router.get("/")
async def list_games(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Game).order_by(Game.name))
    games = result.scalars().all()
    return [g.__dict__ for g in games]

@router.get("/rankings")
async def get_rankings(country: str = "US", platform: str = "ios"):
    return await sensor_tower_service.get_all_rankings_today(country, platform)

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

@router.get("/{app_id}")
async def get_game(app_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Game).where(Game.app_id == app_id))
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return game.__dict__

@router.post("/")
async def create_game(data: GameCreate, db: AsyncSession = Depends(get_db)):
    exists = await db.execute(select(Game).where(Game.app_id == data.app_id))
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Game already exists")
    game = Game(**data.model_dump())
    db.add(game)
    await db.commit()
    await db.refresh(game)
    return game.__dict__

@router.get("/{app_id}/metrics")
async def get_game_metrics(app_id: str, days: int = 30, country: str = "WW", platform: str = "ios"):
    rankings = await sensor_tower_service.get_rankings(app_id, country="US", platform=platform, days=days)
    downloads = await sensor_tower_service.get_downloads(app_id, country=country, platform=platform, days=days)
    revenue = await sensor_tower_service.get_revenue(app_id, country=country, platform=platform, days=days)
    return {"rankings": rankings, "downloads": downloads, "revenue": revenue}
