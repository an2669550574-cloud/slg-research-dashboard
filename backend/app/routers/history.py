from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models.history import GameHistory
from app.models.game import Game
from app.services.ai_history import generate_history

router = APIRouter(prefix="/api/history", tags=["history"])

class HistoryCreate(BaseModel):
    app_id: str
    event_date: str
    event_type: str
    title: str
    description: Optional[str] = None
    source: str = "manual"

@router.get("/{app_id}")
async def get_history(app_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(GameHistory).where(GameHistory.app_id == app_id).order_by(GameHistory.event_date)
    )
    return [h.__dict__ for h in result.scalars().all()]

@router.post("/sync/{app_id}")
async def sync_history(app_id: str, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """触发 AI 自动生成发展历程"""
    game_result = await db.execute(select(Game).where(Game.app_id == app_id))
    game = game_result.scalar_one_or_none()
    name = game.name if game else app_id
    publisher = game.publisher if game else ""

    events = await generate_history(app_id, name, publisher)

    # 清除旧的 AI 生成数据，保留手动录入
    await db.execute(
        delete(GameHistory).where(GameHistory.app_id == app_id, GameHistory.source != "manual")
    )
    for event in events:
        h = GameHistory(
            app_id=app_id,
            event_date=event.get("event_date", ""),
            event_type=event.get("event_type", "version"),
            title=event.get("title", ""),
            description=event.get("description", ""),
            source="ai",
        )
        db.add(h)
    await db.commit()
    return {"message": f"已同步 {len(events)} 条历程数据"}

@router.post("/")
async def create_history(data: HistoryCreate, db: AsyncSession = Depends(get_db)):
    h = GameHistory(**data.model_dump())
    db.add(h)
    await db.commit()
    await db.refresh(h)
    return h.__dict__

@router.delete("/{history_id}")
async def delete_history(history_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(GameHistory).where(GameHistory.id == history_id))
    h = result.scalar_one_or_none()
    if h:
        await db.delete(h)
        await db.commit()
    return {"message": "deleted"}
