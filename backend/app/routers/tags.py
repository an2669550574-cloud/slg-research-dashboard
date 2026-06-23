"""标签库 CRUD（P1）：一级标签(dimension) + 二级标签(option) 管理。

打标签 / 分面筛选 / 聚合分析在后续期。删除走管理员口令 gate（方案 b）。
SQLite 默认不强制 FK 级联，故删除一级 / 二级时在应用层显式连带清理
子表 + 已打标记（material_tag_values），与 product.py 的删除套路一致。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct, delete as sa_delete, update as sa_update
from sqlalchemy.orm import aliased
from typing import Optional

from app.database import get_db
from app.models.material import Material
from app.models.tag import (
    TagDimension, TagOption, MaterialTagValue,
    TagDimensionProduct, TagOptionProduct,
)
from app.schemas import (
    TagDimensionCreate, TagDimensionUpdate, TagDimensionOut,
    TagOptionCreate, TagOptionUpdate, TagOptionOut,
    TagAggregateOut, TagAggregateBucket, TagAggregateSubBucket,
)
from app.security import require_admin_password
from app.services import tagging

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


async def _dim_app_ids(dim_ids: list[int], db: AsyncSession) -> dict[int, list[str]]:
    """{dimension_id: [app_id, ...]}。空列表 = 通用维度（无作用域名单）。"""
    if not dim_ids:
        return {}
    rows = (await db.execute(
        select(TagDimensionProduct.dimension_id, TagDimensionProduct.app_id)
        .where(TagDimensionProduct.dimension_id.in_(dim_ids))
    )).all()
    by_dim: dict[int, list[str]] = {}
    for did, aid in rows:
        by_dim.setdefault(did, []).append(aid)
    return by_dim


async def _set_dim_app_ids(dim_id: int, app_ids: list[str], db: AsyncSession) -> None:
    """replace-all 重设某维度的产品作用域名单。空 = 通用。去重 + 保序。"""
    await db.execute(sa_delete(TagDimensionProduct).where(TagDimensionProduct.dimension_id == dim_id))
    seen: set[str] = set()
    for aid in app_ids:
        aid = (aid or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        db.add(TagDimensionProduct(dimension_id=dim_id, app_id=aid))


async def _opt_app_ids(opt_ids: list[int], db: AsyncSession) -> dict[int, list[str]]:
    """{option_id: [app_id, ...]}。空列表 = 通用选项（无作用域名单）。"""
    if not opt_ids:
        return {}
    rows = (await db.execute(
        select(TagOptionProduct.option_id, TagOptionProduct.app_id)
        .where(TagOptionProduct.option_id.in_(opt_ids))
    )).all()
    by_opt: dict[int, list[str]] = {}
    for oid, aid in rows:
        by_opt.setdefault(oid, []).append(aid)
    return by_opt


async def _set_opt_app_ids(opt_id: int, app_ids: list[str], db: AsyncSession) -> None:
    """replace-all 重设某二级标签的产品作用域名单。空 = 通用。"""
    await db.execute(sa_delete(TagOptionProduct).where(TagOptionProduct.option_id == opt_id))
    seen: set[str] = set()
    for aid in app_ids:
        aid = (aid or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        db.add(TagOptionProduct(option_id=opt_id, app_id=aid))


async def _options_of(
    dim_ids: list[int], db: AsyncSession, app_id: Optional[str] = None,
) -> dict[int, list[TagOption]]:
    """{dim_id: [option, ...]}。给 app_id 时按选项作用域过滤：「无名单 OR 名单含目标」。"""
    if not dim_ids:
        return {}
    rows = (await db.execute(
        select(TagOption).where(TagOption.dimension_id.in_(dim_ids))
        .order_by(TagOption.sort_order, TagOption.id)
    )).scalars().all()
    if app_id and rows:
        opt_app_map = await _opt_app_ids([o.id for o in rows], db)
        rows = [o for o in rows if not opt_app_map.get(o.id) or app_id in opt_app_map[o.id]]
    by_dim: dict[int, list[TagOption]] = {}
    for o in rows:
        by_dim.setdefault(o.dimension_id, []).append(o)
    return by_dim


def _opt_out(o: TagOption, app_ids: list[str] | None = None) -> TagOptionOut:
    out = TagOptionOut.model_validate(o)
    out.app_ids = list(app_ids or [])
    return out


def _dim_out(
    d: TagDimension, options: list[TagOption],
    app_ids: list[str] | None = None,
    opt_app_map: dict[int, list[str]] | None = None,
) -> TagDimensionOut:
    out = TagDimensionOut.model_validate(d)
    out.options = [_opt_out(o, (opt_app_map or {}).get(o.id, [])) for o in options]
    out.app_ids = list(app_ids or [])
    return out


# ── 一级标签 ───────────────────────────────────────────────────────────────

@router.get("/dimensions", response_model=list[TagDimensionOut])
async def list_dimensions(
    material_type: Optional[str] = None,
    app_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """列出一级标签（含其二级标签嵌套 + 产品作用域名单）。

    - material_type 给定 → 返回「该类型 + 通用」（同既有）。
    - app_id 给定（打标签 / 浏览态）→ 按维度作用域过滤：「无名单 OR 名单含该 app_id」。
      不给（管理态）→ 返回全部，UI 据 `app_ids` 字段渲染「通用 / N 个产品」徽标。
    """
    stmt = select(TagDimension).order_by(TagDimension.sort_order, TagDimension.id)
    if material_type:
        stmt = stmt.where(
            (TagDimension.material_type == material_type) | (TagDimension.material_type.is_(None))
        )
    dims = (await db.execute(stmt)).scalars().all()
    app_ids_by_dim = await _dim_app_ids([d.id for d in dims], db)
    if app_id:
        # 「无名单 OR 名单含该 app_id」= 通用维度 + 显式适用该产品的维度
        dims = [d for d in dims if not app_ids_by_dim.get(d.id) or app_id in app_ids_by_dim[d.id]]
    # 选项作用域（S2）：打标签态按 app_id 收敛二级标签；管理态返回全量并附 app_ids
    opts = await _options_of([d.id for d in dims], db, app_id=app_id)
    all_opt_ids = [o.id for olist in opts.values() for o in olist]
    opt_app_map = await _opt_app_ids(all_opt_ids, db) if all_opt_ids else {}
    return [
        _dim_out(d, opts.get(d.id, []), app_ids_by_dim.get(d.id, []), opt_app_map)
        for d in dims
    ]


@router.get("/aggregate", response_model=TagAggregateOut)
async def aggregate_by_dimension(
    dimension_id: int = Query(..., description="主维度：按其二级标签统计素材分布"),
    by: Optional[int] = Query(None, description="可选第二维度 id → 交叉透视（每个主桶再细分）"),
    app_id: Optional[str] = None,
    material_type: Optional[str] = None,
    tag_options: Optional[str] = Query(None, description="与素材列表同口径的分面筛选；先圈定 scope 再聚合"),
    db: AsyncSession = Depends(get_db),
):
    """聚合分析（P4）：按某文字型一级标签统计素材分布，可选第二维度做交叉透视。

    - 桶含计数为 0 的二级标签（呈现完整标签库口径，不只命中项）。
    - count 为「去重素材数」：多选维度下一个素材命中多个二级标签会分别计入各桶，
      故各桶之和可能 > tagged_materials，这是多标签分布的预期语义。
    - scope 过滤（app_id/material_type/tag_options）与素材列表一致，先圈再聚。
    纯本地 SQLite 聚合，零 Sensor Tower 配额。"""
    dim = await _get_dim_or_404(dimension_id, db)
    if dim.value_type != "text":
        raise HTTPException(status_code=400, detail="聚合分析仅支持「文字」型一级标签")
    by_dim: Optional[TagDimension] = None
    if by is not None:
        if by == dimension_id:
            raise HTTPException(status_code=400, detail="交叉维度不能与主维度相同")
        by_dim = await _get_dim_or_404(by, db)
        if by_dim.value_type != "text":
            raise HTTPException(status_code=400, detail="交叉维度仅支持「文字」型一级标签")

    # scope：满足范围过滤的素材 id 集合（含分面筛选，复用 P3 helper）
    scope = select(Material.id)
    if app_id:
        scope = scope.where(Material.app_id == app_id)
    if material_type:
        scope = scope.where(Material.material_type == material_type)
    scope = await tagging.apply_facet_filter(db, scope, tag_options)

    total = (await db.execute(
        select(func.count()).select_from(scope.subquery())
    )).scalar_one()
    tagged = (await db.execute(
        select(func.count(distinct(MaterialTagValue.material_id))).where(
            MaterialTagValue.dimension_id == dimension_id,
            MaterialTagValue.material_id.in_(scope),
        )
    )).scalar_one()

    prim_map = dict((await db.execute(
        select(MaterialTagValue.option_id, func.count(distinct(MaterialTagValue.material_id)))
        .where(
            MaterialTagValue.dimension_id == dimension_id,
            MaterialTagValue.material_id.in_(scope),
        )
        .group_by(MaterialTagValue.option_id)
    )).all())

    # 桶的标签全集（S3）：选了 app_id 时按选项作用域收敛——名单外选项不进桶，
    # 与 Materials 分面栏 / 打标签编辑器口径一致。
    prim_opts = (await _options_of([dimension_id], db, app_id=app_id)).get(dimension_id, [])
    cross_map: dict[tuple[int, int], int] = {}
    by_opts: list[TagOption] = []
    if by_dim is not None:
        by_opts = (await _options_of([by], db, app_id=app_id)).get(by, [])
        A, B = aliased(MaterialTagValue), aliased(MaterialTagValue)
        cross_map = {
            (p, s): n for p, s, n in (await db.execute(
                select(A.option_id, B.option_id, func.count(distinct(A.material_id)))
                .join(B, B.material_id == A.material_id)
                .where(
                    A.dimension_id == dimension_id,
                    B.dimension_id == by,
                    A.material_id.in_(scope),
                )
                .group_by(A.option_id, B.option_id)
            )).all()
        }

    buckets = [
        TagAggregateBucket(
            option_id=o.id, value=o.value, count=prim_map.get(o.id, 0),
            sub=[
                TagAggregateSubBucket(option_id=so.id, value=so.value,
                                      count=cross_map.get((o.id, so.id), 0))
                for so in by_opts
            ] if by_dim is not None else None,
        )
        for o in prim_opts
    ]
    return TagAggregateOut(
        dimension_id=dim.id, dimension_name=dim.name,
        by_dimension_id=by_dim.id if by_dim else None,
        by_dimension_name=by_dim.name if by_dim else None,
        total_materials=total, tagged_materials=tagged, buckets=buckets,
    )


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
    await db.flush()  # 拿 d.id，下面写 junction
    if data.app_ids:
        await _set_dim_app_ids(d.id, data.app_ids, db)
    await db.commit()
    await db.refresh(d)
    return _dim_out(d, [], list(data.app_ids))


@router.put("/dimensions/{dim_id}", response_model=TagDimensionOut)
async def update_dimension(dim_id: int, data: TagDimensionUpdate, db: AsyncSession = Depends(get_db)):
    d = await _get_dim_or_404(dim_id, db)
    # app_ids: None=不动；[]=改为通用；非空=replace-all 重设
    patch = data.model_dump(exclude_none=True)
    new_app_ids = patch.pop("app_ids", None)
    if "name" in patch:
        patch["name"] = patch["name"].strip()
    for k, v in patch.items():
        setattr(d, k, v)
    if new_app_ids is not None:
        await _set_dim_app_ids(dim_id, new_app_ids, db)
    await db.commit()
    await db.refresh(d)
    opts = await _options_of([d.id], db)
    app_ids = (await _dim_app_ids([d.id], db)).get(d.id, [])
    return _dim_out(d, opts.get(d.id, []), app_ids)


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
    # 先清选项作用域（关联到将被删的选项），再删选项本身
    opt_ids = (await db.execute(
        select(TagOption.id).where(TagOption.dimension_id == dim_id)
    )).scalars().all()
    if opt_ids:
        await db.execute(sa_delete(TagOptionProduct).where(TagOptionProduct.option_id.in_(opt_ids)))
    await db.execute(sa_delete(TagOption).where(TagOption.dimension_id == dim_id))
    await db.execute(sa_delete(TagDimensionProduct).where(TagDimensionProduct.dimension_id == dim_id))
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
    await db.flush()  # 拿 o.id 给 junction 用
    if data.app_ids:
        await _set_opt_app_ids(o.id, data.app_ids, db)
    await db.commit()
    await db.refresh(o)
    return _opt_out(o, list(data.app_ids))


@router.put("/options/{opt_id}", response_model=TagOptionOut)
async def update_option(opt_id: int, data: TagOptionUpdate, db: AsyncSession = Depends(get_db)):
    o = await _get_opt_or_404(opt_id, db)
    patch = data.model_dump(exclude_none=True)
    new_app_ids = patch.pop("app_ids", None)
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
    if new_app_ids is not None:
        await _set_opt_app_ids(opt_id, new_app_ids, db)
    await db.commit()
    await db.refresh(o)
    app_ids = (await _opt_app_ids([opt_id], db)).get(opt_id, [])
    return _opt_out(o, app_ids)


@router.delete("/options/{opt_id}", dependencies=_admin)
async def delete_option(opt_id: int, db: AsyncSession = Depends(get_db)):
    """删除二级标签：连带用到它的已打标记一并移除。"""
    o = await _get_opt_or_404(opt_id, db)
    used = (await db.execute(
        select(func.count()).select_from(MaterialTagValue).where(MaterialTagValue.option_id == opt_id)
    )).scalar() or 0
    await db.execute(sa_delete(MaterialTagValue).where(MaterialTagValue.option_id == opt_id))
    await db.execute(sa_delete(TagOptionProduct).where(TagOptionProduct.option_id == opt_id))
    await db.delete(o)
    await db.commit()
    return {"message": "已删除", "id": opt_id, "removed_material_tags": used}
