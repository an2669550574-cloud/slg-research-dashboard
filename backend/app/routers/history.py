from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.database import get_db
from app.models.history import GameHistory
from app.services.history_builder import build_history
from app.schemas import HistoryCreate, HistoryOut
from app.rate_limit import limiter
from app.config import settings

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("/{app_id}", response_model=list[HistoryOut])
async def get_history(app_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(GameHistory).where(GameHistory.app_id == app_id).order_by(GameHistory.event_date)
    )
    return result.scalars().all()


@router.post("/sync/{app_id}")
@limiter.limit(lambda: settings.RATE_LIMIT_AI_SYNC)
async def sync_history(
    request: Request,
    response: Response,
    app_id: str,
    db: AsyncSession = Depends(get_db),
):
    """从 iTunes 元信息 + 本地 game_rankings 拼出**事实性**发展历程。

    slowapi(headers_enabled=True)在 handler 跑完后要把 X-RateLimit-* 注入
    响应——它按名字找 `response: Response` 参数；缺这个参数就会抛
    "parameter `response` must be an instance of ..."。`request`/`response`
    名字是 slowapi 硬约定，不能改。
    """
    events = await build_history(app_id, db)

    # 清除旧的自动生成数据，保留手动录入
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
            source="data",
        )
        db.add(h)
    await db.commit()
    return {"message": f"已同步 {len(events)} 条历程数据"}


@router.post("/", response_model=HistoryOut, status_code=201)
async def create_history(data: HistoryCreate, db: AsyncSession = Depends(get_db)):
    h = GameHistory(**data.model_dump())
    db.add(h)
    await db.commit()
    await db.refresh(h)
    return h


@router.delete("/{history_id}")
async def delete_history(history_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(GameHistory).where(GameHistory.id == history_id))
    h = result.scalar_one_or_none()
    if h:
        await db.delete(h)
        await db.commit()
    return {"message": "deleted"}
