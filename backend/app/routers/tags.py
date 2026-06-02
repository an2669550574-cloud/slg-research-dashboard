"""标签库 CRUD（P1）：一级标签(dimension) + 二级标签(option) 管理。

打标签 / 分面筛选 / 聚合分析在后续期。删除走管理员口令 gate（方案 b）。
SQLite 默认不强制 FK 级联，故删除一级 / 二级时在应用层显式连带清理
子表 + 已打标记（material_tag_values），与 product.py 的删除套路一致。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete as sa_delete, update as sa_update
from typing import Optional

from app.database import get_db
from app.models.tag import TagDimension, TagOption, MaterialTagValue
from app.schemas import (
    TagDimensionCreate, TagDimensionUpdate, TagDimensionOut,
    TagOptionCreate, TagOptionUpdate, TagOptionOut,
)
from app.security import require_admin_password

router = APIRouter(prefix="/api/tags", tags=["tags"])

# 删除接口的管理员口令依赖（未配置 ADMIN_DELETE_PASSWORD 则放行，兼容本地开发）
_admin = [Depends(require_admin_password)]


async def _get_dim_or_404(dim_id: int, db: AsyncSession) -> TagDimension:
    d = (await db.execute(select(TagDimension).where(TagDimension.id == dim_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="一级标签不存在")
    return d


async def _get_opt_or_404(opt_id: int, db: AsyncSession) -> TagOption:
    o = (await db.execute(select(TagOption).where(TagOption.id == opt_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(status_code=404, detail="二级标签不存在")
    return o


async def _options_of(dim_ids: list[int], db: AsyncSession) -> dict[int, list[TagOption]]:
    if not dim_ids:
        return {}
    rows = (await db.execute(
        select(TagOption).where(TagOption.dimension_id.in_(dim_ids))
        .order_by(TagOption.sort_order, TagOption.id)
    )).scalars().all()
    by_dim: dict[int, list[TagOption]] = {}
    for o in rows:
        by_dim.setdefault(o.dimension_id, []).append(o)
    return by_dim


def _dim_out(d: TagDimension, options: list[TagOption]) -> TagDimensionOut:
    out = TagDimensionOut.model_validate(d)
    out.options = [TagOptionOut.model_validate(o) for o in options]
    return out


# ── 一级标签 ───────────────────────────────────────────────────────────────

@router.get("/dimensions", response_model=list[TagDimensionOut])
async def list_dimensions(
    material_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """列出一级标签（含其二级标签嵌套）。material_type 给定时返回「该类型 + 通用」。"""
    stmt = select(TagDimension).order_by(TagDimension.sort_order, TagDimension.id)
    if material_type:
        stmt = stmt.where(
            (TagDimension.material_type == material_type) | (TagDimension.material_type.is_(None))
        )
    dims = (await db.execute(stmt)).scalars().all()
    opts = await _options_of([d.id for d in dims], db)
    return [_dim_out(d, opts.get(d.id, [])) for d in dims]


@router.post("/dimensions", response_model=TagDimensionOut, status_code=201)
async def create_dimension(data: TagDimensionCreate, db: AsyncSession = Depends(get_db)):
    d = TagDimension(
        name=data.name.strip(),
        value_type=data.value_type,
        material_type=data.material_type,
        is_required=data.is_required,
        allow_multi=data.allow_multi,
        sort_order=data.sort_order,
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return _dim_out(d, [])


@router.put("/dimensions/{dim_id}", response_model=TagDimensionOut)
async def update_dimension(dim_id: int, data: TagDimensionUpdate, db: AsyncSession = Depends(get_db)):
    d = await _get_dim_or_404(dim_id, db)
    patch = data.model_dump(exclude_none=True)
    if "name" in patch:
        patch["name"] = patch["name"].strip()
    for k, v in patch.items():
        setattr(d, k, v)
    await db.commit()
    await db.refresh(d)
    opts = await _options_of([d.id], db)
    return _dim_out(d, opts.get(d.id, []))


@router.delete("/dimensions/{dim_id}", dependencies=_admin)
async def delete_dimension(dim_id: int, db: AsyncSession = Depends(get_db)):
    """删除一级标签：连带其二级标签 + 已打标记一并移除（应用层显式级联）。"""
    d = await _get_dim_or_404(dim_id, db)
    used = (await db.execute(
        select(func.count()).select_from(MaterialTagValue).where(MaterialTagValue.dimension_id == dim_id)
    )).scalar() or 0
    opt_n = (await db.execute(
        select(func.count()).select_from(TagOption).where(TagOption.dimension_id == dim_id)
    )).scalar() or 0
    await db.execute(sa_delete(MaterialTagValue).where(MaterialTagValue.dimension_id == dim_id))
    await db.execute(sa_delete(TagOption).where(TagOption.dimension_id == dim_id))
    await db.delete(d)
    await db.commit()
    return {"message": "已删除", "id": dim_id, "removed_options": opt_n, "removed_material_tags": used}


# ── 二级标签 ───────────────────────────────────────────────────────────────

@router.post("/dimensions/{dim_id}/options", response_model=TagOptionOut, status_code=201)
async def create_option(dim_id: int, data: TagOptionCreate, db: AsyncSession = Depends(get_db)):
    d = await _get_dim_or_404(dim_id, db)
    if d.value_type != "text":
        raise HTTPException(status_code=400, detail="只有「文字」型一级标签可添加二级标签（「时间」型在打标签时选日期）")
    value = data.value.strip()
    dup = (await db.execute(
        select(TagOption).where(TagOption.dimension_id == dim_id, TagOption.value == value)
    )).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail="同一级标签下已存在该二级标签")
    o = TagOption(dimension_id=dim_id, value=value, sort_order=data.sort_order)
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


@router.put("/options/{opt_id}", response_model=TagOptionOut)
async def update_option(opt_id: int, data: TagOptionUpdate, db: AsyncSession = Depends(get_db)):
    o = await _get_opt_or_404(opt_id, db)
    patch = data.model_dump(exclude_none=True)
    if "value" in patch:
        new_value = patch["value"].strip()
        dup = (await db.execute(
            select(TagOption).where(
                TagOption.dimension_id == o.dimension_id,
                TagOption.value == new_value,
                TagOption.id != opt_id,
            )
        )).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=409, detail="同一级标签下已存在该二级标签")
        o.value = new_value
        # 同步刷新已打标记里冗余存的 value（聚合口径一致）
        await db.execute(
            sa_update(MaterialTagValue).where(MaterialTagValue.option_id == opt_id).values(value=new_value)
        )
    if "sort_order" in patch:
        o.sort_order = patch["sort_order"]
    await db.commit()
    await db.refresh(o)
    return o


@router.delete("/options/{opt_id}", dependencies=_admin)
async def delete_option(opt_id: int, db: AsyncSession = Depends(get_db)):
    """删除二级标签：连带用到它的已打标记一并移除。"""
    o = await _get_opt_or_404(opt_id, db)
    used = (await db.execute(
        select(func.count()).select_from(MaterialTagValue).where(MaterialTagValue.option_id == opt_id)
    )).scalar() or 0
    await db.execute(sa_delete(MaterialTagValue).where(MaterialTagValue.option_id == opt_id))
    await db.delete(o)
    await db.commit()
    return {"message": "已删除", "id": opt_id, "removed_material_tags": used}
