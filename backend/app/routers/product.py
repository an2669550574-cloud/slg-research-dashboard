from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.database import get_db
from app.models.product import OwnProduct
from app.schemas import OwnProductOut, OwnProductCreate, OwnProductUpdate

router = APIRouter(prefix="/api/products", tags=["products"])


async def _clear_other_defaults(db: AsyncSession, keep_id: int | None) -> None:
    """把 is_default=True 收敛到至多一条：清掉除 keep_id 外的所有默认标记。"""
    stmt = update(OwnProduct).values(is_default=False).where(OwnProduct.is_default.is_(True))
    if keep_id is not None:
        stmt = stmt.where(OwnProduct.id != keep_id)
    await db.execute(stmt)


@router.get("/", response_model=list[OwnProductOut])
async def list_products(db: AsyncSession = Depends(get_db)):
    """默认产品排在最前，其余按最近更新降序——前端打开即取 items[0] 作默认带入。"""
    res = await db.execute(
        select(OwnProduct).order_by(
            OwnProduct.is_default.desc(), OwnProduct.updated_at.desc()
        )
    )
    return res.scalars().all()


@router.post("/", response_model=OwnProductOut, status_code=201)
async def create_product(data: OwnProductCreate, db: AsyncSession = Depends(get_db)):
    p = OwnProduct(name=data.name, brief=data.brief, is_default=data.is_default)
    db.add(p)
    await db.flush()  # 拿到 p.id 再收敛其它默认
    if p.is_default:
        await _clear_other_defaults(db, keep_id=p.id)
    await db.commit()
    await db.refresh(p)
    return p


@router.put("/{product_id}", response_model=OwnProductOut)
async def update_product(
    product_id: int, data: OwnProductUpdate, db: AsyncSession = Depends(get_db)
):
    p = (await db.execute(select(OwnProduct).where(OwnProduct.id == product_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="产品不存在")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    if p.is_default:
        await _clear_other_defaults(db, keep_id=p.id)
    await db.commit()
    await db.refresh(p)
    return p


@router.delete("/{product_id}")
async def delete_product(product_id: int, db: AsyncSession = Depends(get_db)):
    p = (await db.execute(select(OwnProduct).where(OwnProduct.id == product_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="产品不存在")
    await db.delete(p)
    await db.commit()
    return {"message": "deleted", "id": product_id}
