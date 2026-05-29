from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from typing import Optional, Literal
from urllib.parse import quote
from app.config import settings
from app.database import get_db, utcnow_naive
from app.models.material import Material
from app.schemas import MaterialCreate, MaterialUpdate, MaterialOut, MaterialTagCount
from pydantic import BaseModel, Field
from app.services import creative_adapt, media, video_analyze

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


def _to_out(m: Material) -> MaterialOut:
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
    return out


@router.get("/", response_model=list[MaterialOut])
async def list_materials(
    response: Response,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = None,
    platform: Optional[str] = None,
    material_type: Optional[str] = None,
    tag: Optional[str] = Query(None, description="精确匹配某个标签（素材 tags 含该标签）"),
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
    if q:
        like = f"%{q}%"
        base = base.where((Material.title.ilike(like)) | (Material.notes.ilike(like)))

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    response.headers["X-Total-Count"] = str(total)

    sort_col = MATERIAL_SORT_FIELDS[sort_by]
    base = base.order_by(sort_col.desc() if order == "desc" else sort_col.asc())
    base = base.limit(limit).offset(offset)
    result = await db.execute(base)
    return [_to_out(m) for m in result.scalars().all()]


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
    """外链素材。"""
    m = Material(**data.model_dump(), source="link")
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return _to_out(m)


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
):
    """部门自有素材上传：校验类型/大小、流式落盘、入库。"""
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
    await db.commit()
    await db.refresh(m)
    return _to_out(m)


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
    return _to_out(m)


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

    return _to_out(m)


@router.get("/{material_id}", response_model=MaterialOut)
async def get_material(material_id: int, db: AsyncSession = Depends(get_db)):
    """单素材详情；前端轮询分析状态用。"""
    m = (await db.execute(select(Material).where(Material.id == material_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="素材不存在")
    return _to_out(m)


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
        return _to_out(m)
    existing = list(m.tags or [])
    seen = set(existing)
    for t in m.analysis_tags:
        if t and t not in seen:
            existing.append(t)
            seen.add(t)
    m.tags = existing
    await db.commit()
    await db.refresh(m)
    return _to_out(m)


# ── 创意迁移（两段式：方向 → 脚本，人在中间筛）───────────────────────

class AdaptDirectionsReq(BaseModel):
    our_product: str = Field(..., min_length=1, max_length=4000,
                             description="自家产品 brief，自由文本")


class AdaptScriptReq(BaseModel):
    our_product: str = Field(..., min_length=1, max_length=4000)
    direction: dict = Field(..., description="阶段 1 输出的某个方向对象")


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
    return {"data": result.data, "cost_usd": result.cost_usd, "model": result.model}


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
