from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete as sa_delete
from typing import Optional

from app.config import settings
from app.database import get_db
from app.models.product import OwnProduct, OwnProductMaterial
from app.schemas import (
    OwnProductOut, OwnProductCreate, OwnProductUpdate,
    OwnProductMaterialOut, OwnProductMaterialTextCreate, OwnProductAnalyzeResult,
)
from app.services import media, product_analyze, video_analyze

router = APIRouter(prefix="/api/products", tags=["products"])
# 产品素材文件流：站内 <video>/<img> 预览，src 带不了 X-API-Key 头，故此路由
# **不挂** 全局 require_api_key（main.py 单独 include），改用 HMAC 短时令牌。
file_router = APIRouter(prefix="/api/products", tags=["products"])


async def _clear_other_defaults(db: AsyncSession, keep_id: int | None) -> None:
    """把 is_default=True 收敛到至多一条：清掉除 keep_id 外的所有默认标记。"""
    stmt = update(OwnProduct).values(is_default=False).where(OwnProduct.is_default.is_(True))
    if keep_id is not None:
        stmt = stmt.where(OwnProduct.id != keep_id)
    await db.execute(stmt)


async def _get_product_or_404(product_id: int, db: AsyncSession) -> OwnProduct:
    p = (await db.execute(select(OwnProduct).where(OwnProduct.id == product_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="产品不存在")
    return p


def _mat_out(m: OwnProductMaterial) -> OwnProductMaterialOut:
    out = OwnProductMaterialOut.model_validate(m)
    if m.file_path:
        out.preview_url = f"/api/products/materials/{m.id}/file?token={media.sign(m.id)}"
    return out


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
    p = OwnProduct(name=data.name, brief=data.brief,
                   match_keywords=data.match_keywords,
                   match_subgenre=data.match_subgenre, is_default=data.is_default)
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
    p = await _get_product_or_404(product_id, db)
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    if p.is_default:
        await _clear_other_defaults(db, keep_id=p.id)
    await db.commit()
    await db.refresh(p)
    return p


@router.delete("/{product_id}")
async def delete_product(product_id: int, db: AsyncSession = Depends(get_db)):
    p = await _get_product_or_404(product_id, db)
    # 先收集子素材落盘文件 → 删子行 → 删产品 → 提交后再删档（避免删档后回滚成孤儿）。
    # SQLite 默认不强制 FK 级联，故应用层显式删子素材。
    mats = (await db.execute(
        select(OwnProductMaterial).where(OwnProductMaterial.own_product_id == product_id)
    )).scalars().all()
    file_paths = [m.file_path for m in mats if m.file_path]
    await db.execute(sa_delete(OwnProductMaterial).where(OwnProductMaterial.own_product_id == product_id))
    await db.delete(p)
    await db.commit()
    for fp in file_paths:
        media.delete_file(fp)
    return {"message": "deleted", "id": product_id}


# ── 自有产品素材 ─────────────────────────────────────────────────────────

@router.get("/{product_id}/materials", response_model=list[OwnProductMaterialOut])
async def list_product_materials(product_id: int, db: AsyncSession = Depends(get_db)):
    await _get_product_or_404(product_id, db)
    rows = (await db.execute(
        select(OwnProductMaterial)
        .where(OwnProductMaterial.own_product_id == product_id)
        .order_by(OwnProductMaterial.created_at.desc())
    )).scalars().all()
    return [_mat_out(m) for m in rows]


@router.post("/{product_id}/materials/upload", response_model=OwnProductMaterialOut, status_code=201)
async def upload_product_material(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
):
    """上传视频 / 图片素材（asset_type 由文件实际类型判定）。"""
    await _get_product_or_404(product_id, db)
    info = await media.save_upload(file)  # 校验白名单 + 大小 + 流式落盘；kind=video/image
    m = OwnProductMaterial(
        own_product_id=product_id,
        asset_type=info["kind"],
        title=(title or "").strip() or None,
        file_path=info["file_path"], file_name=info["file_name"],
        file_size=info["file_size"], mime_type=info["mime_type"],
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return _mat_out(m)


@router.post("/{product_id}/materials/text", response_model=OwnProductMaterialOut, status_code=201)
async def add_product_material_text(
    product_id: int, data: OwnProductMaterialTextCreate, db: AsyncSession = Depends(get_db)
):
    """纯文字素材：商店描述 / 产品介绍等。"""
    await _get_product_or_404(product_id, db)
    m = OwnProductMaterial(
        own_product_id=product_id,
        asset_type="text",
        title=(data.title or "").strip() or None,
        text_content=data.text_content,
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return _mat_out(m)


@router.delete("/{product_id}/materials/{material_id}")
async def delete_product_material(
    product_id: int, material_id: int, db: AsyncSession = Depends(get_db)
):
    m = (await db.execute(
        select(OwnProductMaterial).where(
            OwnProductMaterial.id == material_id,
            OwnProductMaterial.own_product_id == product_id,
        )
    )).scalar_one_or_none()
    if m:
        file_path = m.file_path
        await db.delete(m)
        await db.commit()
        media.delete_file(file_path)  # DB 删成功后再删档
    return {"message": "deleted"}


@router.post("/{product_id}/analyze", response_model=OwnProductAnalyzeResult)
async def analyze_product_endpoint(product_id: int, db: AsyncSession = Depends(get_db)):
    """同步解析该产品挂的素材 → 反推产品画像 → 返回 brief 草稿（不自动写库）。

    护栏：与素材分析共享 LLM_DAILY_BUDGET_USD 日预算前置检查。本解析不写
    materials 表、不计入 today_cost 聚合（低频小额），仅前置拦预算耗尽的当天。
    """
    product = await _get_product_or_404(product_id, db)
    materials = list((await db.execute(
        select(OwnProductMaterial).where(OwnProductMaterial.own_product_id == product_id)
    )).scalars().all())
    if not materials:
        raise HTTPException(status_code=400, detail="该产品还没有素材，请先上传宣传片/截图或粘贴商店描述")

    spent = await video_analyze.today_cost_usd(db)
    if spent >= settings.LLM_DAILY_BUDGET_USD:
        raise HTTPException(
            status_code=429,
            detail=f"今日 LLM 预算已用尽（${spent:.2f} / ${settings.LLM_DAILY_BUDGET_USD:.2f}），明日重试",
        )
    try:
        r = await product_analyze.analyze_product(product, materials)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"产品解析失败：{type(e).__name__}: {str(e)[:200]}")
    return OwnProductAnalyzeResult(
        brief=r.brief, theme=r.theme, gameplay=r.gameplay,
        selling_points=r.selling_points, audience=r.audience,
        differentiation=r.differentiation, cost_usd=r.cost_usd,
        model=r.model, material_count=r.material_count,
    )


# ── 素材文件流（HMAC 令牌鉴权，不挂全局 require_api_key）──────────────────

@file_router.get("/materials/{material_id}/file")
async def serve_product_material_file(
    material_id: int,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """站内播放/预览自有产品素材。HMAC 令牌鉴权 + Range（视频可拖拽）。"""
    if not media.verify(material_id, token):
        raise HTTPException(status_code=403, detail="无效或过期的访问令牌")
    m = (await db.execute(
        select(OwnProductMaterial).where(OwnProductMaterial.id == material_id)
    )).scalar_one_or_none()
    if not m or not m.file_path:
        raise HTTPException(status_code=404, detail="文件不存在")
    path = media.resolve(m.file_path)
    return media.stream_file(path, m.mime_type, m.file_name, request.headers.get("range"))
