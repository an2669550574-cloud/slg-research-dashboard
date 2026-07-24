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
    TagPack, TagPackDimension, TagPackOption, TagPackProduct, TagPackSetting,
)
from app.schemas import (
    TagDimensionCreate, TagDimensionUpdate, TagDimensionOut,
    TagOptionCreate, TagOptionUpdate, TagOptionOut,
    TagScopeBatchInput, TagScopeBatchOut,
    TagTemplateCopyInput, TagTemplateCopyOut,
    TagReorderInput, TagReorderOutput,
    TagPackCreate, TagPackUpdate, TagPackOut,
    TagPackSettingOut, TagPackSettingPut,
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


@router.put("/dimensions/reorder", response_model=TagReorderOutput)
async def reorder_dimensions(data: TagReorderInput, db: AsyncSession = Depends(get_db)):
    """重排一级标签顺序（标签库「上移/下移/置顶」）。前端传完整维度 id 顺序，
    后端按下标写 sort_order=0..N-1。任一 id 不存在 → 404 整体回滚（不静默）。

    **必须先于 `PUT /dimensions/{dim_id}` 声明**（字面量段惯例，否则 'reorder' 被当
    dim_id 走 int 转换失败返 422）。
    """
    ids = data.ordered_ids
    if not ids:
        return TagReorderOutput(reordered=0)
    existing = set((await db.execute(select(TagDimension.id))).scalars().all())
    missing = [i for i in ids if i not in existing]
    if missing:
        raise HTTPException(status_code=404, detail=f"一级标签不存在：{missing}")
    for idx, did in enumerate(ids):
        await db.execute(
            sa_update(TagDimension).where(TagDimension.id == did).values(sort_order=idx)
        )
    await db.commit()
    return TagReorderOutput(reordered=len(ids))


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
        await db.execute(sa_delete(TagPackOption).where(TagPackOption.option_id.in_(opt_ids)))
    await db.execute(sa_delete(TagOption).where(TagOption.dimension_id == dim_id))
    await db.execute(sa_delete(TagDimensionProduct).where(TagDimensionProduct.dimension_id == dim_id))
    # 标签包成员关系一并摘除（包本身保留，允许空包）
    await db.execute(sa_delete(TagPackDimension).where(TagPackDimension.dimension_id == dim_id))
    await db.delete(d)
    await db.commit()
    return {"message": "已删除", "id": dim_id, "removed_options": opt_n, "removed_material_tags": used}


# ── 产品视角批量改作用域（S4）─────────────────────────────────────────────

@router.put("/scope/batch", response_model=TagScopeBatchOut)
async def update_scope_batch(data: TagScopeBatchInput, db: AsyncSession = Depends(get_db)):
    """「产品视角」一次性提交多条作用域改动（维度 + 选项），原子事务。

    每条对某维度/选项的 app_ids 做 replace-all，与单条 PUT 同语义；前端只发改动行。
    任一 id 不存在 → 404 整体回滚（不静默跳过，避免前端脏状态被掩盖）。
    """
    if data.dimensions:
        dim_ids = [it.id for it in data.dimensions]
        found = set((await db.execute(
            select(TagDimension.id).where(TagDimension.id.in_(dim_ids))
        )).scalars().all())
        missing = [i for i in dim_ids if i not in found]
        if missing:
            raise HTTPException(status_code=404, detail=f"一级标签不存在：{missing}")
    if data.options:
        opt_ids = [it.id for it in data.options]
        found_o = set((await db.execute(
            select(TagOption.id).where(TagOption.id.in_(opt_ids))
        )).scalars().all())
        missing_o = [i for i in opt_ids if i not in found_o]
        if missing_o:
            raise HTTPException(status_code=404, detail=f"二级标签不存在：{missing_o}")

    for it in data.dimensions:
        await _set_dim_app_ids(it.id, it.app_ids, db)
    for it in data.options:
        await _set_opt_app_ids(it.id, it.app_ids, db)
    await db.commit()
    return TagScopeBatchOut(
        updated_dimensions=len(data.dimensions),
        updated_options=len(data.options),
    )


@router.post("/copy-template", response_model=TagTemplateCopyOut)
async def copy_template(data: TagTemplateCopyInput, db: AsyncSession = Depends(get_db)):
    """以源产品的**专属**维度为模板，克隆一套给目标产品（新品建标签库场景）。

    语义刻意选「克隆」而非「共享作用域」：复制后两边独立演进——给新产品增删选项
    不会污染源产品词表；代价是管理态出现同名维度，靠作用域徽标区分。
    - 只复制显式作用域**含源产品**的维度；通用维度（空名单）对目标本就可见，
      复制反而会让目标看到双份，跳过。
    - 目标已有同名可见维度（通用或已挂目标）→ 跳过并报告（幂等，防双击双份）。
    - include_options 时连二级选项复制（value/sort_order）；**选项作用域不复制**
      ——克隆出的维度已是目标专属，选项再挂名单是冗余门禁。
    - 单事务：任何失败整体回滚，不留半套。
    """
    src, tgt = data.source_app_id.strip(), data.target_app_id.strip()
    if not src or not tgt or src == tgt:
        raise HTTPException(status_code=400, detail="源产品与目标产品必须是两个不同的 app_id")

    dims = (await db.execute(
        select(TagDimension).order_by(TagDimension.sort_order, TagDimension.id)
    )).scalars().all()
    scope_map = await _dim_app_ids([d.id for d in dims], db)
    # 目标当前可见的维度名（通用 + 显式挂目标），同名即跳过
    visible_to_tgt = {
        d.name for d in dims
        if not scope_map.get(d.id) or tgt in scope_map[d.id]
    }
    sources = [d for d in dims if scope_map.get(d.id) and src in scope_map[d.id]]
    if not sources:
        raise HTTPException(status_code=404, detail="源产品没有专属维度可作模板")

    copied: list[str] = []
    skipped: list[str] = []
    options_copied = 0
    src_opts = await _options_of([d.id for d in sources], db) if data.include_options else {}
    for d in sources:
        if d.name in visible_to_tgt:
            skipped.append(d.name)
            continue
        clone = TagDimension(
            name=d.name, value_type=d.value_type, material_type=d.material_type,
            is_required=d.is_required, allow_multi=d.allow_multi, sort_order=d.sort_order,
        )
        db.add(clone)
        await db.flush()  # 拿 clone.id 挂作用域/选项
        db.add(TagDimensionProduct(dimension_id=clone.id, app_id=tgt))
        for o in src_opts.get(d.id, []):
            db.add(TagOption(dimension_id=clone.id, value=o.value, sort_order=o.sort_order))
            options_copied += 1
        copied.append(d.name)
    await db.commit()
    return TagTemplateCopyOut(copied=copied, skipped=skipped, options_copied=options_copied)


# ── 标签包（tag pack）──────────────────────────────────────────────────────
# 把一级标签分组成自定义大类（如「物资链路」「投放要点」）。包是视图不是分区：
# 一个维度可同属多个包。素材库按包切分面视图；产品级开关（tag_pack_settings）
# 决定某产品是否启用包视图，无记录 = 默认关。


async def _pack_or_404(pack_id: int, db: AsyncSession) -> TagPack:
    p = (await db.execute(select(TagPack).where(TagPack.id == pack_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="标签包不存在")
    return p


async def _pack_dim_ids(pack_ids: list[int], db: AsyncSession) -> dict[int, list[int]]:
    """{pack_id: [dimension_id, ...]}，按加入顺序（id）稳定输出。"""
    if not pack_ids:
        return {}
    rows = (await db.execute(
        select(TagPackDimension.pack_id, TagPackDimension.dimension_id)
        .where(TagPackDimension.pack_id.in_(pack_ids))
        .order_by(TagPackDimension.id)
    )).all()
    by_pack: dict[int, list[int]] = {}
    for pid, did in rows:
        by_pack.setdefault(pid, []).append(did)
    return by_pack


async def _set_pack_dim_ids(pack_id: int, dim_ids: list[int], db: AsyncSession) -> None:
    """replace-all 重设包的成员维度。任一 id 不存在 → 404 整体回滚（不静默跳过）。"""
    uniq: list[int] = []
    seen: set[int] = set()
    for did in dim_ids:
        if did not in seen:
            seen.add(did)
            uniq.append(did)
    if uniq:
        found = set((await db.execute(
            select(TagDimension.id).where(TagDimension.id.in_(uniq))
        )).scalars().all())
        missing = [i for i in uniq if i not in found]
        if missing:
            raise HTTPException(status_code=404, detail=f"一级标签不存在：{missing}")
    await db.execute(sa_delete(TagPackDimension).where(TagPackDimension.pack_id == pack_id))
    for did in uniq:
        db.add(TagPackDimension(pack_id=pack_id, dimension_id=did))


async def _pack_opt_ids(pack_ids: list[int], db: AsyncSession) -> dict[int, list[int]]:
    """{pack_id: [option_id, ...]}（选项子集成员，0047），按加入顺序稳定输出。"""
    if not pack_ids:
        return {}
    rows = (await db.execute(
        select(TagPackOption.pack_id, TagPackOption.option_id)
        .where(TagPackOption.pack_id.in_(pack_ids))
        .order_by(TagPackOption.id)
    )).all()
    by_pack: dict[int, list[int]] = {}
    for pid, oid in rows:
        by_pack.setdefault(pid, []).append(oid)
    return by_pack


async def _set_pack_opt_ids(pack_id: int, opt_ids: list[int], db: AsyncSession) -> None:
    """replace-all 重设包的选项子集成员。任一 id 不存在 → 404 整体回滚。"""
    uniq: list[int] = []
    seen: set[int] = set()
    for oid in opt_ids:
        if oid not in seen:
            seen.add(oid)
            uniq.append(oid)
    if uniq:
        found = set((await db.execute(
            select(TagOption.id).where(TagOption.id.in_(uniq))
        )).scalars().all())
        missing = [i for i in uniq if i not in found]
        if missing:
            raise HTTPException(status_code=404, detail=f"二级标签不存在：{missing}")
    await db.execute(sa_delete(TagPackOption).where(TagPackOption.pack_id == pack_id))
    for oid in uniq:
        db.add(TagPackOption(pack_id=pack_id, option_id=oid))


async def _normalize_pack_options(pack_id: int, db: AsyncSession) -> None:
    """归一：同包同维度「整维度 vs 选项子集」互斥，整维度优先——
    摘除父维度已整包含的选项子集行。create/update 提交前统一调一次。"""
    await db.execute(
        sa_delete(TagPackOption).where(
            TagPackOption.pack_id == pack_id,
            TagPackOption.option_id.in_(
                select(TagOption.id)
                .join(TagPackDimension, TagPackDimension.dimension_id == TagOption.dimension_id)
                .where(TagPackDimension.pack_id == pack_id)
            ),
        )
    )


async def _pack_app_ids(pack_ids: list[int], db: AsyncSession) -> dict[int, list[str]]:
    """{pack_id: [app_id, ...]}。空列表 = 通用包（无作用域名单）。"""
    if not pack_ids:
        return {}
    rows = (await db.execute(
        select(TagPackProduct.pack_id, TagPackProduct.app_id)
        .where(TagPackProduct.pack_id.in_(pack_ids))
    )).all()
    by_pack: dict[int, list[str]] = {}
    for pid, aid in rows:
        by_pack.setdefault(pid, []).append(aid)
    return by_pack


async def _set_pack_app_ids(pack_id: int, app_ids: list[str], db: AsyncSession) -> None:
    """replace-all 重设包的产品作用域名单。空 = 通用。去重 + 保序。"""
    await db.execute(sa_delete(TagPackProduct).where(TagPackProduct.pack_id == pack_id))
    seen: set[str] = set()
    for aid in app_ids:
        aid = (aid or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        db.add(TagPackProduct(pack_id=pack_id, app_id=aid))


def _pack_out(p: TagPack, dim_ids: list[int], app_ids: list[str],
              opt_ids: list[int] | None = None) -> TagPackOut:
    out = TagPackOut.model_validate(p)
    out.dimension_ids = list(dim_ids)
    out.option_ids = list(opt_ids or [])
    out.app_ids = list(app_ids)
    return out


async def _ensure_pack_name_free(name: str, db: AsyncSession, exclude_id: int | None = None) -> None:
    """包名唯一（包总量少、名字撞车只会添乱）。"""
    stmt = select(TagPack).where(TagPack.name == name)
    if exclude_id is not None:
        stmt = stmt.where(TagPack.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="已存在同名标签包")


@router.get("/packs", response_model=list[TagPackOut])
async def list_packs(app_id: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """列出标签包（含成员维度 id + 产品作用域名单）。

    - app_id 给定（素材库浏览态）→ 按包作用域过滤：「无名单 OR 名单含该 app_id」。
    - 不给（管理态）→ 返回全部，UI 据 app_ids 渲染「通用 / N 个产品」徽标。
    dimension_ids 恒为全量成员，不按 app_id 收敛——前端自行与可见维度求交集。
    """
    packs = (await db.execute(
        select(TagPack).order_by(TagPack.sort_order, TagPack.id)
    )).scalars().all()
    app_map = await _pack_app_ids([p.id for p in packs], db)
    if app_id:
        packs = [p for p in packs if not app_map.get(p.id) or app_id in app_map[p.id]]
    dim_map = await _pack_dim_ids([p.id for p in packs], db)
    opt_map = await _pack_opt_ids([p.id for p in packs], db)
    return [
        _pack_out(p, dim_map.get(p.id, []), app_map.get(p.id, []), opt_map.get(p.id, []))
        for p in packs
    ]


@router.post("/packs", response_model=TagPackOut, status_code=201)
async def create_pack(data: TagPackCreate, db: AsyncSession = Depends(get_db)):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="包名不能为空")
    await _ensure_pack_name_free(name, db)
    p = TagPack(name=name, sort_order=data.sort_order)
    db.add(p)
    await db.flush()  # 拿 p.id 写成员/作用域
    await _set_pack_dim_ids(p.id, data.dimension_ids, db)
    if data.option_ids:
        await _set_pack_opt_ids(p.id, data.option_ids, db)
        await _normalize_pack_options(p.id, db)  # 整维度优先：已整包含维度的选项剔除
    if data.app_ids:
        await _set_pack_app_ids(p.id, data.app_ids, db)
    await db.commit()
    await db.refresh(p)
    dim_ids = (await _pack_dim_ids([p.id], db)).get(p.id, [])
    opt_ids = (await _pack_opt_ids([p.id], db)).get(p.id, [])
    app_ids = (await _pack_app_ids([p.id], db)).get(p.id, [])
    return _pack_out(p, dim_ids, app_ids, opt_ids)


@router.put("/packs/reorder", response_model=TagReorderOutput)
async def reorder_packs(data: TagReorderInput, db: AsyncSession = Depends(get_db)):
    """重排标签包顺序，语义同 dimensions/reorder（传完整 id 序，按下标写 sort_order）。

    **必须先于 `PUT /packs/{pack_id}` 声明**（字面量段惯例）。
    """
    ids = data.ordered_ids
    if not ids:
        return TagReorderOutput(reordered=0)
    existing = set((await db.execute(select(TagPack.id))).scalars().all())
    missing = [i for i in ids if i not in existing]
    if missing:
        raise HTTPException(status_code=404, detail=f"标签包不存在：{missing}")
    for idx, pid in enumerate(ids):
        await db.execute(sa_update(TagPack).where(TagPack.id == pid).values(sort_order=idx))
    await db.commit()
    return TagReorderOutput(reordered=len(ids))


@router.get("/packs/settings/{app_id}", response_model=TagPackSettingOut)
async def get_pack_setting(app_id: str, db: AsyncSession = Depends(get_db)):
    """查产品级包视图开关。无记录 = 默认关。"""
    row = (await db.execute(
        select(TagPackSetting).where(TagPackSetting.app_id == app_id)
    )).scalar_one_or_none()
    return TagPackSettingOut(app_id=app_id, enabled=bool(row and row.enabled))


@router.put("/packs/settings/{app_id}", response_model=TagPackSettingOut)
async def put_pack_setting(app_id: str, data: TagPackSettingPut, db: AsyncSession = Depends(get_db)):
    """设产品级包视图开关（upsert）。**必须先于 `PUT /packs/{pack_id}` 声明**。"""
    app_id = app_id.strip()
    if not app_id:
        raise HTTPException(status_code=422, detail="app_id 不能为空")
    row = (await db.execute(
        select(TagPackSetting).where(TagPackSetting.app_id == app_id)
    )).scalar_one_or_none()
    if row:
        row.enabled = data.enabled
    else:
        db.add(TagPackSetting(app_id=app_id, enabled=data.enabled))
    await db.commit()
    return TagPackSettingOut(app_id=app_id, enabled=data.enabled)


@router.put("/packs/{pack_id}", response_model=TagPackOut)
async def update_pack(pack_id: int, data: TagPackUpdate, db: AsyncSession = Depends(get_db)):
    p = await _pack_or_404(pack_id, db)
    patch = data.model_dump(exclude_none=True)
    new_dim_ids = patch.pop("dimension_ids", None)
    new_opt_ids = patch.pop("option_ids", None)
    new_app_ids = patch.pop("app_ids", None)
    if "name" in patch:
        name = patch["name"].strip()
        if not name:
            raise HTTPException(status_code=422, detail="包名不能为空")
        await _ensure_pack_name_free(name, db, exclude_id=pack_id)
        p.name = name
    if "sort_order" in patch:
        p.sort_order = patch["sort_order"]
    if new_dim_ids is not None:
        await _set_pack_dim_ids(pack_id, new_dim_ids, db)
    if new_opt_ids is not None:
        await _set_pack_opt_ids(pack_id, new_opt_ids, db)
    if new_dim_ids is not None or new_opt_ids is not None:
        # 归一（整维度优先）：升级为整维度的老选项子集、或新子集撞上整维度，都在这里摘掉
        await _normalize_pack_options(pack_id, db)
    if new_app_ids is not None:
        await _set_pack_app_ids(pack_id, new_app_ids, db)
    await db.commit()
    await db.refresh(p)
    dim_ids = (await _pack_dim_ids([pack_id], db)).get(pack_id, [])
    opt_ids = (await _pack_opt_ids([pack_id], db)).get(pack_id, [])
    app_ids = (await _pack_app_ids([pack_id], db)).get(pack_id, [])
    return _pack_out(p, dim_ids, app_ids, opt_ids)


@router.delete("/packs/{pack_id}")
async def delete_pack(pack_id: int, db: AsyncSession = Depends(get_db)):
    """删标签包：只删分组配置（成员关系 + 作用域名单），**不动任何维度/选项/已打标记**，
    故不走管理员口令 gate（与删维度/选项不同，无数据损失）。"""
    p = await _pack_or_404(pack_id, db)
    n = (await db.execute(
        select(func.count()).select_from(TagPackDimension).where(TagPackDimension.pack_id == pack_id)
    )).scalar() or 0
    await db.execute(sa_delete(TagPackDimension).where(TagPackDimension.pack_id == pack_id))
    await db.execute(sa_delete(TagPackOption).where(TagPackOption.pack_id == pack_id))
    await db.execute(sa_delete(TagPackProduct).where(TagPackProduct.pack_id == pack_id))
    await db.delete(p)
    await db.commit()
    return {"message": "已删除", "id": pack_id, "removed_members": n}


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
    await db.execute(sa_delete(TagPackOption).where(TagPackOption.option_id == opt_id))
    await db.delete(o)
    await db.commit()
    return {"message": "已删除", "id": opt_id, "removed_material_tags": used}
