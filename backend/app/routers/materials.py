from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional, Literal
from app.database import get_db
from app.models.material import Material
from app.schemas import MaterialCreate, MaterialUpdate, MaterialOut

router = APIRouter(prefix="/api/materials", tags=["materials"])

MATERIAL_SORT_FIELDS = {
    "created_at": Material.created_at,
    "title": Material.title,
}


@router.get("/", response_model=list[MaterialOut])
async def list_materials(
    response: Response,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = None,
    platform: Optional[str] = None,
    material_type: Optional[str] = None,
    q: Optional[str] = Query(None, description="模糊匹配 title 或 notes"),
    sort_by: Literal["created_at", "title"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    base = select(Material)
    if app_id:
        base = base.where(Material.app_id == app_id)
    if platform:
        base = base.where(Material.platform == platform)
    if material_type:
        base = base.where(Material.material_type == material_type)
    if q:
        like = f"%{q}%"
        base = base.where((Material.title.ilike(like)) | (Material.notes.ilike(like)))

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    response.headers["X-Total-Count"] = str(total)

    sort_col = MATERIAL_SORT_FIELDS[sort_by]
    base = base.order_by(sort_col.desc() if order == "desc" else sort_col.asc())
    base = base.limit(limit).offset(offset)
    result = await db.execute(base)
    return result.scalars().all()


@router.post("/", response_model=MaterialOut, status_code=201)
async def create_material(data: MaterialCreate, db: AsyncSession = Depends(get_db)):
    m = Material(**data.model_dump())
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


@router.put("/{material_id}", response_model=MaterialOut)
async def update_material(material_id: int, data: MaterialUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Material).where(Material.id == material_id))
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="Material not found")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(m, k, v)
    await db.commit()
    await db.refresh(m)
    return m


@router.delete("/{material_id}")
async def delete_material(material_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Material).where(Material.id == material_id))
    m = result.scalar_one_or_none()
    if m:
        await db.delete(m)
        await db.commit()
    return {"message": "deleted"}
