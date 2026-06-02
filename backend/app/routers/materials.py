import json
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from typing import Optional, Literal
from urllib.parse import quote
from app.config import settings
from app.database import get_db, utcnow_naive
from app.models.material import Material, CreativeAdaptation
from app.schemas import (
    MaterialCreate, MaterialUpdate, MaterialOut, MaterialTagCount,
    MaterialTagValueInput, MaterialTagValuesPut,
)
from pydantic import BaseModel, Field, ValidationError
from app.services import creative_adapt, media, video_analyze, tagging

router = APIRouter(prefix="/api/materials", tags=["materials"])
# 站内播放/预览：<video>/<img> 的 src 带不了 X-API-Key 头，故此路由**不挂**全局
# require_api_key（main.py 单独 include、不加 _protected），改用 HMAC 短时令牌。
file_router = APIRouter(prefix="/api/materials", tags=["materials"])

MATERIAL_SORT_FIELDS = {
    "created_at": Material.created_at,
    "title": Material.title,
    "analyzed_at": Material.analyzed_at,
    "analysis_cost_usd": Material.analysis_cost_usd,
}


def _to_out(m: Material, tag_values=None) -> MaterialOut:
    out = MaterialOut.model_validate(m)
    out.stream_url = media.stream_url(m)
    # 抽帧 + 联系单：URL 走相同 HMAC 令牌（与 stream_url 一致：token=sign(material_id)）
    if m.analysis_frames:
        tok = media.sign(m.id)
        out.analysis_frames = [
            {"ts": f.get("ts"), "url": f"/api/materials/{m.id}/frame/{i}?token={tok}"}
            for i, f in enumerate(m.analysis_frames)
        ]
    if m.analysis_has_contact_sheet:
        tok = media.sign(m.id)
        out.analysis_contact_sheet_url = f"/api/materials/{m.id}/contact-sheet?token={tok}"
    if tag_values is not None:
        out.tag_values = tag_values
    return out


async def _single_out(db: AsyncSession, m: Material) -> MaterialOut:
    """单素材出参：补上其结构化标签（P2）。供 create/get/update/analyze 等单条返回用。"""
    vals = (await tagging.load_tag_values_map(db, [m.id])).get(m.id, [])
    return _to_out(m, tag_values=vals)


@router.get("/", response_model=list[MaterialOut])
async def list_materials(
    response: Response,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = None,
    platform: Optional[str] = None,
    material_type: Optional[str] = None,
    tag: Optional[str] = Query(None, description="精确匹配某个标签（素材 tags 含该标签）"),
    tag_options: Optional[str] = Query(
        None,
        description="结构化二级标签分面筛选：二级标签 id 逗号分隔；同一维度内 OR、跨维度 AND",
    ),
    q: Optional[str] = Query(None, description="模糊匹配 title 或 notes"),
    analysis_status: Optional[Literal["pending", "running", "done", "failed"]] = Query(
        None, description="按 LLM 分析状态筛选；AI 分析报告页用 done 拉已分析素材"
    ),
    sort_by: Literal["created_at", "title", "analyzed_at", "analysis_cost_usd"] = "created_at",
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
    if analysis_status:
        base = base.where(Material.analysis_status == analysis_status)
    if tag:
        # tags 是 JSON 数组；用 SQLite JSON1 的 json_each 做"数组含某值"判定。
        # json_valid 兜空/脏数据，避免 json_each(NULL) 报错。
        base = base.where(
            text(
                "json_valid(materials.tags) AND EXISTS ("
                "SELECT 1 FROM json_each(materials.tags) "
                "WHERE json_each.value = :tag)"
            ).bindparams(tag=tag)
        )
    # 结构化分面筛选（P3）：同维度内 OR、跨维度 AND（与聚合分析 P4 共用同一 helper）。
    base = await tagging.apply_facet_filter(db, base, tag_options)
    if q:
        like = f"%{q}%"
        base = base.where((Material.title.ilike(like)) | (Material.notes.ilike(like)))

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    response.headers["X-Total-Count"] = str(total)

    sort_col = MATERIAL_SORT_FIELDS[sort_by]
    base = base.order_by(sort_col.desc() if order == "desc" else sort_col.asc())
    base = base.limit(limit).offset(offset)
    result = await db.execute(base)
    items = result.scalars().all()
    tv_map = await tagging.load_tag_values_map(db, [m.id for m in items])
    return [_to_out(m, tag_values=tv_map.get(m.id, [])) for m in items]


@router.get("/tags", response_model=list[MaterialTagCount])
async def list_material_tags(
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = None,
):
    """全部标签 + 各自素材数，按热度降序。供前端标签筛选栏用——
    纯本地 SQLite 聚合，零 Sensor Tower 配额。"""
    sql = (
        "SELECT je.value AS tag, COUNT(*) AS n "
        "FROM materials, json_each(materials.tags) je "
        "WHERE json_valid(materials.tags)"
    )
    params: dict = {}
    if app_id:
        sql += " AND materials.app_id = :app_id"
        params["app_id"] = app_id
    sql += " GROUP BY je.value ORDER BY n DESC, je.value ASC"
    rows = (await db.execute(text(sql), params)).all()
    return [MaterialTagCount(tag=r[0], count=r[1]) for r in rows]


@router.post("/", response_model=MaterialOut, status_code=201)
async def create_material(data: MaterialCreate, db: AsyncSession = Depends(get_db)):
    """外链素材。tag_values 随建一并打，必填维度缺失则回滚拒绝。"""
    payload = data.model_dump()
    tag_values = [MaterialTagValueInput(**v) for v in payload.pop("tag_values", [])]
    m = Material(**payload, source="link")
    db.add(m)
    await db.flush()  # 拿到 m.id 给标签关联用，但先不提交
    try:
        await tagging.set_material_tag_values(db, m, tag_values)
    except ValueError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    await db.refresh(m)
    return await _single_out(db, m)


@router.post("/upload", response_model=MaterialOut, status_code=201)
async def upload_material(
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
    title: str = Form(...),
    app_id: str = Form(""),
    platform: Optional[str] = Form(None),
    material_type: str = Form("video"),
    tags: str = Form(""),
    notes: Optional[str] = Form(None),
    tag_values: str = Form("", description="结构化标签 JSON：[{dimension_id, option_ids, value_date}]"),
):
    """部门自有素材上传：校验类型/大小、流式落盘、入库。

    tag_values 是 JSON 字符串（multipart 带不了嵌套对象）；批量上传时同一组
    结构化标签套到每个文件。必填维度缺失则删档回滚拒绝（与 create 一致）。"""
    try:
        raw_tv = json.loads(tag_values) if tag_values.strip() else []
        parsed_tv = [MaterialTagValueInput(**v) for v in raw_tv]
    except (json.JSONDecodeError, ValidationError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"结构化标签格式错误：{e}")

    info = await media.save_upload(file)
    if material_type not in ("video", "image"):
        material_type = info["kind"]
    if material_type != info["kind"]:
        media.delete_file(info["file_path"])
        raise HTTPException(
            status_code=400,
            detail=f"素材类型({material_type})与文件实际类型({info['kind']})不符",
        )
    m = Material(
        app_id=app_id, title=title, url=None, source="upload",
        file_path=info["file_path"], file_name=info["file_name"],
        file_size=info["file_size"], mime_type=info["mime_type"],
        platform=platform, material_type=material_type,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        notes=notes,
    )
    db.add(m)
    await db.flush()
    try:
        await tagging.set_material_tag_values(db, m, parsed_tv)
    except ValueError as e:
        await db.rollback()
        media.delete_file(info["file_path"])  # 落盘已发生，校验失败须删档免留孤儿
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    await db.refresh(m)
    return await _single_out(db, m)


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
    return await _single_out(db, m)


@router.post("/{material_id}/analyze", response_model=MaterialOut)
async def analyze_material_endpoint(
    material_id: int,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """触发 LLM 视频分析（异步）。立刻返回 status=running 的素材状态。

    护栏：
    - 仅 upload 来源的视频可分析（外链拿不到原文件抽帧）
    - 日成本超 LLM_DAILY_BUDGET_USD 拒新请求（避免失控烧钱）
    - 同素材正在分析中（status=running）则拒绝重入；走 done/failed 都允许重分析

    后台 task 会写回 done/failed 状态；前端 GET /{id} 轮询拉新状态。
    """
    m = (await db.execute(select(Material).where(Material.id == material_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="素材不存在")
    if m.source != "upload" or not m.file_path:
        raise HTTPException(status_code=400, detail="仅上传素材可分析；外链素材请下载后上传")
    if m.material_type != "video":
        raise HTTPException(status_code=400, detail=f"仅视频可分析（当前类型：{m.material_type}）")
    if m.analysis_status == "running":
        raise HTTPException(status_code=409, detail="分析进行中，请稍候")

    spent = await video_analyze.today_cost_usd(db)
    if spent >= settings.LLM_DAILY_BUDGET_USD:
        raise HTTPException(
            status_code=429,
            detail=f"今日 LLM 预算已用尽（${spent:.2f} / ${settings.LLM_DAILY_BUDGET_USD:.2f}），明日重试"
        )

    m.analysis_status = "running"
    m.analysis_error = None
    await db.commit()
    await db.refresh(m)

    background.add_task(video_analyze.analyze_material, material_id)

    return await _single_out(db, m)


@router.get("/{material_id}", response_model=MaterialOut)
async def get_material(material_id: int, db: AsyncSession = Depends(get_db)):
    """单素材详情；前端轮询分析状态用。"""
    m = (await db.execute(select(Material).where(Material.id == material_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="素材不存在")
    return await _single_out(db, m)


@router.put("/{material_id}/tag-values", response_model=MaterialOut)
async def set_tag_values(
    material_id: int, data: MaterialTagValuesPut, db: AsyncSession = Depends(get_db),
):
    """整体替换某素材的结构化标签（P2，replace-all）。必填/单多选/归属校验失败 → 400。"""
    m = (await db.execute(select(Material).where(Material.id == material_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="素材不存在")
    try:
        await tagging.set_material_tag_values(db, m, data.values)
    except ValueError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return await _single_out(db, m)


@router.post("/{material_id}/adopt-tags", response_model=MaterialOut)
async def adopt_analysis_tags(material_id: int, db: AsyncSession = Depends(get_db)):
    """把 LLM 提议的 analysis_tags 合并进人工 tags（去重保序）。

    LLM 标签默认不污染人工 tags（避免误判进既有筛选体系）；用户审视后点
    "采纳"才入库。再次分析覆盖 analysis_tags 不影响已采纳的人工 tags。
    """
    m = (await db.execute(select(Material).where(Material.id == material_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="素材不存在")
    if not m.analysis_tags:
        return await _single_out(db, m)
    existing = list(m.tags or [])
    seen = set(existing)
    for t in m.analysis_tags:
        if t and t not in seen:
            existing.append(t)
            seen.add(t)
    m.tags = existing
    await db.commit()
    await db.refresh(m)
    return await _single_out(db, m)


# ── 创意迁移（两段式：方向 → 脚本，人在中间筛）───────────────────────

class AdaptDirectionsReq(BaseModel):
    our_product: str = Field(..., min_length=1, max_length=4000,
                             description="自家产品 brief，自由文本")
    product_id: Optional[int] = Field(None, description="来源「我方产品」档案 id（手输则为空）")


class AdaptScriptReq(BaseModel):
    our_product: str = Field(..., min_length=1, max_length=4000)
    direction: dict = Field(..., description="阶段 1 输出的某个方向对象")
    adaptation_id: Optional[int] = Field(None, description="所属历史存档 id；带上则把脚本回写该行")
    direction_index: Optional[int] = Field(None, description="选定方向在 directions 数组中的下标")


def _adaptation_out(r: CreativeAdaptation) -> dict:
    """历史存档转出。data 字段刻意复刻阶段 1 端点的 {directions, constraints_check}
    形状，让前端复用同一套渲染组件。"""
    return {
        "id": r.id,
        "material_id": r.material_id,
        "our_product": r.our_product,
        "product_id": r.product_id,
        "data": {"directions": r.directions or [], "constraints_check": r.constraints_check},
        "model": r.model,
        "cost_usd": r.cost_usd,
        "chosen_index": r.chosen_index,
        "chosen_name": r.chosen_name,
        "script": r.script,
        "script_model": r.script_model,
        "script_cost_usd": r.script_cost_usd,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "script_updated_at": r.script_updated_at.isoformat() if r.script_updated_at else None,
    }


@router.post("/{material_id}/adapt/directions")
async def adapt_directions(
    material_id: int,
    req: AdaptDirectionsReq,
    db: AsyncSession = Depends(get_db),
):
    """阶段 1：生成 3-5 个创意方向。要求素材已 analysis_status=done。

    护栏：与 analyze 共享 LLM_DAILY_BUDGET_USD 日预算（同张账号下游开销都计）。
    """
    m = (await db.execute(select(Material).where(Material.id == material_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="素材不存在")
    if m.analysis_status != "done":
        raise HTTPException(status_code=400, detail="请先完成素材分析（点击 ✨ 分析）")
    spent = await video_analyze.today_cost_usd(db)
    if spent >= settings.LLM_DAILY_BUDGET_USD:
        raise HTTPException(
            status_code=429,
            detail=f"今日 LLM 预算已用尽（${spent:.2f} / ${settings.LLM_DAILY_BUDGET_USD:.2f}），明日重试"
        )
    try:
        result = await creative_adapt.generate_directions(m, req.our_product)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # 网关/网络/解析失败：返回 4xx/5xx 让前端展示，不污染 analysis_* 字段
        raise HTTPException(status_code=502, detail=f"方向生成失败：{type(e).__name__}: {str(e)[:200]}")
    # 自动落库进历史（花了钱的成品不丢）；写库失败不该吞掉已生成的结果，故宽容处理。
    row = CreativeAdaptation(
        material_id=m.id,
        our_product=req.our_product.strip(),
        product_id=req.product_id,
        directions=result.data.get("directions") or [],
        constraints_check=result.data.get("constraints_check"),
        model=result.model,
        cost_usd=result.cost_usd,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "data": result.data, "cost_usd": result.cost_usd, "model": result.model}


@router.post("/{material_id}/adapt/script")
async def adapt_script(
    material_id: int,
    req: AdaptScriptReq,
    db: AsyncSession = Depends(get_db),
):
    """阶段 2：基于选中方向写详细分镜脚本。"""
    m = (await db.execute(select(Material).where(Material.id == material_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="素材不存在")
    if m.analysis_status != "done":
        raise HTTPException(status_code=400, detail="请先完成素材分析")
    spent = await video_analyze.today_cost_usd(db)
    if spent >= settings.LLM_DAILY_BUDGET_USD:
        raise HTTPException(
            status_code=429,
            detail=f"今日 LLM 预算已用尽（${spent:.2f} / ${settings.LLM_DAILY_BUDGET_USD:.2f}），明日重试"
        )
    try:
        result = await creative_adapt.generate_script(m, req.our_product, req.direction)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"脚本生成失败：{type(e).__name__}: {str(e)[:200]}")
    # 回写脚本到所属历史存档（只留最后一次脚本）；存档不存在则静默跳过（仍返回结果）。
    if req.adaptation_id is not None:
        row = (await db.execute(
            select(CreativeAdaptation).where(
                CreativeAdaptation.id == req.adaptation_id,
                CreativeAdaptation.material_id == m.id,
            )
        )).scalar_one_or_none()
        if row is not None:
            row.chosen_index = req.direction_index
            row.chosen_name = req.direction.get("name")
            row.script = result.data
            row.script_model = result.model
            row.script_cost_usd = result.cost_usd
            row.script_updated_at = utcnow_naive()
            await db.commit()
    return {"data": result.data, "cost_usd": result.cost_usd, "model": result.model}


@router.get("/{material_id}/adaptations")
async def list_adaptations(material_id: int, db: AsyncSession = Depends(get_db)):
    """列出某素材的创意迁移历史（最新在前）。零 LLM 开销，纯读本地库。"""
    rows = (await db.execute(
        select(CreativeAdaptation)
        .where(CreativeAdaptation.material_id == material_id)
        .order_by(CreativeAdaptation.created_at.desc(), CreativeAdaptation.id.desc())
    )).scalars().all()
    return [_adaptation_out(r) for r in rows]


@router.delete("/adaptations/{adaptation_id}")
async def delete_adaptation(adaptation_id: int, db: AsyncSession = Depends(get_db)):
    """删除一条创意迁移历史存档。"""
    row = (await db.execute(
        select(CreativeAdaptation).where(CreativeAdaptation.id == adaptation_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="历史存档不存在")
    await db.delete(row)
    await db.commit()
    return {"message": "已删除", "id": adaptation_id}


# ── 跨素材统一方向（选项 C：勾选 N 支 done 素材 → 归纳共性 → 统一方向）──

class UnifiedDirectionsReq(BaseModel):
    material_ids: list[int] = Field(..., min_length=2, max_length=15,
                                    description="勾选的已分析素材 id（2-15 支）")
    our_product: str = Field(..., min_length=1, max_length=4000)
    model: str = Field("claude-sonnet-4.5",
                       description="claude-sonnet-4.5（默认）/ claude-opus-4.7")


async def _load_unified_materials(material_ids: list[int], db: AsyncSession) -> list[Material]:
    """按请求顺序加载素材；缺任何一个直接 404（避免静默少算）。"""
    ids = list(dict.fromkeys(material_ids))  # 去重保序
    rows = (await db.execute(select(Material).where(Material.id.in_(ids)))).scalars().all()
    by_id = {m.id: m for m in rows}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail=f"素材不存在：{missing}")
    return [by_id[i] for i in ids]


@router.post("/adapt/unified-directions")
async def adapt_unified_directions(
    req: UnifiedDirectionsReq,
    estimate_only: bool = Query(False, description="true 则只返回预估成本，不调 LLM"),
    db: AsyncSession = Depends(get_db),
):
    """选项 C：归纳 N 支已分析素材的共性 → 统一创意方向。

    - estimate_only=true：干跑，只数 token 返回预估成本，不烧配额、不查日预算。
    - 否则：与 analyze/adapt 共享 LLM_DAILY_BUDGET_USD 日预算护栏。
    模型白名单 + 数量/分析状态校验在 service 层兜底，这里把 ValueError 转 400。
    """
    materials = await _load_unified_materials(req.material_ids, db)

    if estimate_only:
        try:
            return creative_adapt.estimate_unified_cost(materials, req.our_product, req.model)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    spent = await video_analyze.today_cost_usd(db)
    if spent >= settings.LLM_DAILY_BUDGET_USD:
        raise HTTPException(
            status_code=429,
            detail=f"今日 LLM 预算已用尽（${spent:.2f} / ${settings.LLM_DAILY_BUDGET_USD:.2f}），明日重试"
        )
    try:
        result = await creative_adapt.generate_unified_directions(
            materials, req.our_product, req.model
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"统一方向生成失败：{type(e).__name__}: {str(e)[:200]}")
    return {"data": result.data, "cost_usd": result.cost_usd, "model": result.model}


@router.delete("/{material_id}")
async def delete_material(material_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Material).where(Material.id == material_id))
    m = result.scalar_one_or_none()
    if m:
        file_path = m.file_path if m.source == "upload" else None
        await db.delete(m)
        await db.commit()
        media.delete_file(file_path)  # DB 删除成功后再删档，避免删档后回滚成孤儿
    return {"message": "deleted"}


_STREAM_CHUNK = 1 << 18  # 256KB


def _file_iter(path, start: int, end: int):
    """读 [start, end] 闭区间，分块 yield。"""
    with open(path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = f.read(min(_STREAM_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@file_router.get("/{material_id}/file")
async def serve_material_file(
    material_id: int,
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """站内播放/预览。HMAC 令牌鉴权 + 手写 Range（视频可拖拽进度条）。

    必须自己实现 Range：本 app 挂了 RequestLoggingMiddleware(BaseHTTPMiddleware)，
    且此版本 Starlette FileResponse 不发 Accept-Ranges，浏览器无法 seek 大视频。
    StreamingResponse 分块吐，避免把 200MB 整个读进内存。
    """
    if not media.verify(material_id, token):
        raise HTTPException(status_code=403, detail="无效或过期的访问令牌")
    result = await db.execute(select(Material).where(Material.id == material_id))
    m = result.scalar_one_or_none()
    if not m or m.source != "upload" or not m.file_path:
        raise HTTPException(status_code=404, detail="文件不存在")
    path = media.resolve(m.file_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件已丢失")

    size = path.stat().st_size
    mime = m.mime_type or "application/octet-stream"
    # HTTP 头只能 latin-1：中文/非 ASCII 文件名直接塞 filename= 会让 uvicorn
    # 发响应头时 UnicodeEncodeError → 整个取流 500（视频永远播不了）。
    # 按 RFC 6266：filename= 给 ASCII 兜底，filename*=UTF-8'' 带真实名。
    raw_name = m.file_name or path.name
    ascii_name = "".join(
        c for c in raw_name.encode("ascii", "ignore").decode()
        if c.isprintable() and c not in '"\\'
    ).strip() or "file"
    disposition = f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(raw_name)}"
    common = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": disposition,
        "Cache-Control": "private, max-age=3600",
    }
    range_header = request.headers.get("range")
    if range_header and range_header.startswith("bytes="):
        spec = range_header[6:].split(",")[0].strip()
        s, _, e = spec.partition("-")
        try:
            start = int(s) if s else 0
            end = int(e) if e else size - 1
        except ValueError:
            raise HTTPException(status_code=416, detail="Invalid Range")
        end = min(end, size - 1)
        if start > end or start >= size:
            raise HTTPException(
                status_code=416, detail="Range Not Satisfiable",
                headers={"Content-Range": f"bytes */{size}"},
            )
        return StreamingResponse(
            _file_iter(path, start, end), status_code=206, media_type=mime,
            headers={**common, "Content-Range": f"bytes {start}-{end}/{size}",
                     "Content-Length": str(end - start + 1)},
        )
    return StreamingResponse(
        _file_iter(path, 0, size - 1), media_type=mime,
        headers={**common, "Content-Length": str(size)},
    )


# ── 分析 artifact 静态文件（HMAC 令牌，与 /file 同一鉴权口径）─────────────

def _serve_jpeg(path) -> StreamingResponse:
    """简单 inline JPEG 流式响应。artifact 都是 ≤500KB 的小图，不做 Range。"""
    if not path.is_file():
        raise HTTPException(status_code=404, detail="artifact 不存在（请重新分析）")
    size = path.stat().st_size
    return StreamingResponse(
        _file_iter(path, 0, size - 1),
        media_type="image/jpeg",
        headers={
            "Content-Length": str(size),
            "Cache-Control": "private, max-age=86400",  # 1 天；token 本身 TTL 决定上限
        },
    )


@file_router.get("/{material_id}/contact-sheet")
async def serve_contact_sheet(material_id: int, token: Optional[str] = Query(None)):
    """联系单 JPG：5 列 N 行的关键帧拼图，由 video_analyze.build_contact_sheet 生成。"""
    if not media.verify(material_id, token):
        raise HTTPException(status_code=403, detail="无效或过期的访问令牌")
    return _serve_jpeg(video_analyze.contact_sheet_path(material_id))


@file_router.get("/{material_id}/frame/{n}")
async def serve_frame(material_id: int, n: int, token: Optional[str] = Query(None)):
    """单帧 JPG：frame_NN.jpg，n 是 0-indexed 帧索引。"""
    if not media.verify(material_id, token):
        raise HTTPException(status_code=403, detail="无效或过期的访问令牌")
    if n < 0 or n > 99:  # 帧名格式 NN，最多两位
        raise HTTPException(status_code=400, detail="frame index 越界")
    return _serve_jpeg(video_analyze.frame_path(material_id, n))
