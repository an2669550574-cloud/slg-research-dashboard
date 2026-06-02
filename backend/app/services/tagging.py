"""结构化打标签的核心逻辑（P2）：把素材在各一级标签维度下选定的值落库 /
读出 / 校验。抽出来供 materials 路由（create / upload / edit / 单读 / 列表）共用，
避免路由层互相 import。纯本地 SQLite，零 Sensor Tower 配额。

打标签语义：
- text 维度：选 TagOption（allow_multi 决定单/多选），按 option_id 落行、冗余存 value
- date 维度：选具体日期，落 value_date（无 option）
- is_required 维度：create / upload / 替换时必须有值，否则 ValueError（路由转 400）
replace-all：每次写入先清掉该素材旧的全部 material_tag_values 再重建。
"""
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material
from app.models.tag import TagDimension, TagOption, MaterialTagValue
from app.schemas import MaterialTagValueItem, MaterialTagValueInput


def _parse_option_ids(tag_options: str | None) -> list[int]:
    """把逗号分隔的二级标签 id 串解析为整数列表，脏值/空段静默忽略（不 500）。"""
    if not tag_options:
        return []
    return [int(p) for p in tag_options.split(",") if p.strip().lstrip("-").isdigit()]


async def apply_facet_filter(db: AsyncSession, base, tag_options: str | None):
    """给一个以 Material 为根的 select 追加结构化分面筛选（P3/P4 共用）：
    选中的二级标签按所属一级标签分组，同维度内 OR、跨维度 AND——每个维度一条
    EXISTS 相关子查询（素材须命中该维度任一选项）。无有效 id 时原样返回。"""
    opt_ids = _parse_option_ids(tag_options)
    if not opt_ids:
        return base
    by_dim: dict[int, list[int]] = {}
    for oid, did in (await db.execute(
        select(TagOption.id, TagOption.dimension_id).where(TagOption.id.in_(opt_ids))
    )).all():
        by_dim.setdefault(did, []).append(oid)
    for oids in by_dim.values():
        base = base.where(
            select(MaterialTagValue.id).where(
                MaterialTagValue.material_id == Material.id,
                MaterialTagValue.option_id.in_(oids),
            ).exists()
        )
    return base


async def applicable_dimensions(db: AsyncSession, material_type: str | None) -> list[TagDimension]:
    """适用于某素材类型的一级标签：material_type 精确匹配 + 通用(NULL)，按 sort 排。"""
    stmt = select(TagDimension).order_by(TagDimension.sort_order, TagDimension.id)
    if material_type:
        stmt = stmt.where(
            (TagDimension.material_type == material_type) | (TagDimension.material_type.is_(None))
        )
    return list((await db.execute(stmt)).scalars().all())


async def load_tag_values_map(
    db: AsyncSession, material_ids: list[int]
) -> dict[int, list[MaterialTagValueItem]]:
    """批量加载多个素材的已打标记（含维度名/类型，免前端再 join）。

    列表页一次性取整页素材的标记，避免 N+1。按维度 sort、再二级 sort 排，
    让卡片上的 chip 顺序与标签库一致。"""
    if not material_ids:
        return {}
    rows = (await db.execute(
        select(
            MaterialTagValue.material_id,
            MaterialTagValue.dimension_id,
            MaterialTagValue.option_id,
            MaterialTagValue.value,
            MaterialTagValue.value_date,
            TagDimension.name,
            TagDimension.value_type,
        )
        .join(TagDimension, TagDimension.id == MaterialTagValue.dimension_id)
        .outerjoin(TagOption, TagOption.id == MaterialTagValue.option_id)
        .where(MaterialTagValue.material_id.in_(material_ids))
        .order_by(
            TagDimension.sort_order, TagDimension.id,
            TagOption.sort_order, MaterialTagValue.id,
        )
    )).all()
    out: dict[int, list[MaterialTagValueItem]] = {}
    for mid, dim_id, opt_id, value, value_date, dim_name, value_type in rows:
        out.setdefault(mid, []).append(MaterialTagValueItem(
            dimension_id=dim_id, dimension_name=dim_name, value_type=value_type,
            option_id=opt_id, value=value, value_date=value_date,
        ))
    return out


async def set_material_tag_values(
    db: AsyncSession, material: Material, inputs: list[MaterialTagValueInput]
) -> None:
    """整体替换某素材的结构化标签（replace-all）。校验通过后重建行，不在此 commit
    （由调用方与素材本身的写入一并提交，保证原子）。

    校验（失败抛 ValueError，路由转 400）：
    - 维度须存在且适用于该素材类型
    - 二级标签须属于该维度
    - 单选维度不能给多个 option
    - is_required 维度必须有非空值
    """
    dims = await applicable_dimensions(db, material.material_type)
    dim_by_id = {d.id: d for d in dims}

    opt_rows = (await db.execute(
        select(TagOption).where(TagOption.dimension_id.in_(list(dim_by_id.keys())))
    )).scalars().all() if dim_by_id else []
    opt_by_id = {o.id: o for o in opt_rows}

    provided: dict[int, MaterialTagValueInput] = {}
    for inp in inputs:
        d = dim_by_id.get(inp.dimension_id)
        if not d:
            raise ValueError(f"标签维度不存在或不适用于该素材类型（id={inp.dimension_id}）")
        provided[inp.dimension_id] = inp

    new_rows: list[MaterialTagValue] = []
    for dim_id, inp in provided.items():
        d = dim_by_id[dim_id]
        if d.value_type == "date":
            if inp.value_date is None:
                continue  # 可选 date 维度留空 → 不落行
            new_rows.append(MaterialTagValue(
                material_id=material.id, dimension_id=dim_id, value_date=inp.value_date,
            ))
        else:
            ids = list(dict.fromkeys(inp.option_ids))  # 去重保序
            if not ids:
                continue
            if not d.allow_multi and len(ids) > 1:
                raise ValueError(f"标签「{d.name}」为单选，不能选多个")
            for oid in ids:
                o = opt_by_id.get(oid)
                if o is None or o.dimension_id != dim_id:
                    raise ValueError(f"二级标签不属于该维度（option_id={oid}）")
                new_rows.append(MaterialTagValue(
                    material_id=material.id, dimension_id=dim_id, option_id=oid, value=o.value,
                ))

    # 必填校验：required 维度必须在 new_rows 里有体现
    filled = {r.dimension_id for r in new_rows}
    missing = [d.name for d in dims if d.is_required and d.id not in filled]
    if missing:
        raise ValueError("必填标签未填写：" + "、".join(missing))

    await db.execute(sa_delete(MaterialTagValue).where(MaterialTagValue.material_id == material.id))
    db.add_all(new_rows)


async def require_required_satisfied(db: AsyncSession, material: Material) -> None:
    """校验某素材已满足其类型下所有 is_required 维度（用于不带结构化标签的
    create / upload 路径：若库中存在必填维度而未打，直接拒绝）。"""
    dims = await applicable_dimensions(db, material.material_type)
    required = [d for d in dims if d.is_required]
    if not required:
        return
    have = set((await db.execute(
        select(MaterialTagValue.dimension_id).where(MaterialTagValue.material_id == material.id)
    )).scalars().all())
    missing = [d.name for d in required if d.id not in have]
    if missing:
        raise ValueError("必填标签未填写：" + "、".join(missing))
