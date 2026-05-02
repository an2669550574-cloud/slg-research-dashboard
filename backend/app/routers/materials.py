from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models.material import Material

router = APIRouter(prefix="/api/materials", tags=["materials"])

class MaterialCreate(BaseModel):
    app_id: str
    title: str
    url: str
    platform: Optional[str] = None
    material_type: str = "video"
    tags: list[str] = []
    notes: Optional[str] = None

class MaterialUpdate(BaseModel):
    title: Optional[str] = None
    url: Optional[str] = None
    platform: Optional[str] = None
    material_type: Optional[str] = None
    tags: Optional[list[str]] = None
    notes: Optional[str] = None

@router.get("/")
async def list_materials(app_id: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    query = select(Material).order_by(Material.created_at.desc())
    if app_id:
        query = query.where(Material.app_id == app_id)
    result = await db.execute(query)
    return [m.__dict__ for m in result.scalars().all()]

@router.post("/")
async def create_material(data: MaterialCreate, db: AsyncSession = Depends(get_db)):
    m = Material(**data.model_dump())
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m.__dict__

@router.put("/{material_id}")
async def update_material(material_id: int, data: MaterialUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Material).where(Material.id == material_id))
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="Material not found")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(m, k, v)
    await db.commit()
    return m.__dict__

@router.delete("/{material_id}")
async def delete_material(material_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Material).where(Material.id == material_id))
    m = result.scalar_one_or_none()
    if m:
        await db.delete(m)
        await db.commit()
    return {"message": "deleted"}
