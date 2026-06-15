"""/api/wechat-accounts —— 看板维护订阅的行业公众号（新品监测日报按这些号搜文章）。

加号流程：前端用名字调 /search（代理 wechat-api searchbiz）拿候选 → 选中 → POST 落库。
取代原先硬编码在 services/wechat_articles 的列表。零 ST 配额（走 wechat-api，不碰 Sensor Tower）。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.wechat import WechatAccount
from app.schemas.wechat import (
    WechatAccountCandidate, WechatAccountCreate, WechatAccountOut, WechatAccountUpdate,
)
from app.services.wechat_articles import search_biz

router = APIRouter(prefix="/api/wechat-accounts", tags=["wechat"])


@router.get("/", response_model=list[WechatAccountOut])
async def list_accounts(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(WechatAccount).order_by(WechatAccount.id))).scalars().all()
    return rows


@router.get("/search", response_model=list[WechatAccountCandidate])
async def search_accounts(query: str = Query(..., min_length=1), db: AsyncSession = Depends(get_db)):
    """按名搜公众号候选。未启用 wechat / 服务连不上 → 空列表（前端提示）。"""
    if not settings.WECHAT_ENABLED:
        raise HTTPException(status_code=409, detail="微信功能未启用（WECHAT_ENABLED=false）")
    return await search_biz(query.strip())


@router.post("/", response_model=WechatAccountOut, status_code=201)
async def create_account(data: WechatAccountCreate, db: AsyncSession = Depends(get_db)):
    dup = (await db.execute(
        select(WechatAccount).where(WechatAccount.fakeid == data.fakeid))).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail="该公众号已订阅")
    acc = WechatAccount(name=data.name.strip(), fakeid=data.fakeid.strip(), enabled=True)
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return acc


@router.patch("/{account_id}", response_model=WechatAccountOut)
async def update_account(account_id: int, data: WechatAccountUpdate,
                         db: AsyncSession = Depends(get_db)):
    acc = (await db.execute(
        select(WechatAccount).where(WechatAccount.id == account_id))).scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="订阅号不存在")
    acc.enabled = data.enabled
    await db.commit()
    await db.refresh(acc)
    return acc


@router.delete("/{account_id}")
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    acc = (await db.execute(
        select(WechatAccount).where(WechatAccount.id == account_id))).scalar_one_or_none()
    if acc:
        await db.delete(acc)
        await db.commit()
    return {"message": "deleted", "id": account_id}
