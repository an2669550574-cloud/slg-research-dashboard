from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional, Literal
from urllib.parse import quote
from app.database import get_db
from app.models.material import Material
from app.schemas import MaterialCreate, MaterialUpdate, MaterialOut
from app.services import media

router = APIRouter(prefix="/api/materials", tags=["materials"])
# 站内播放/预览：<video>/<img> 的 src 带不了 X-API-Key 头，故此路由**不挂**全局
# require_api_key（main.py 单独 include、不加 _protected），改用 HMAC 短时令牌。
file_router = APIRouter(prefix="/api/materials", tags=["materials"])

MATERIAL_SORT_FIELDS = {
    "created_at": Material.created_at,
    "title": Material.title,
}


def _to_out(m: Material) -> MaterialOut:
    out = MaterialOut.model_validate(m)
    out.stream_url = media.stream_url(m)
    return out


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
    return [_to_out(m) for m in result.scalars().all()]


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
