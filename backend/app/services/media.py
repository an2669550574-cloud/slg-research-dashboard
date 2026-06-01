"""素材文件本地存储 + 站内播放鉴权令牌。

- 上传：白名单后缀、流式落盘（边写边数大小，超限即中止删档），uuid 命名
  防碰撞 / 路径穿越。
- 鉴权：<video>/<img> 的 src 带不了 X-API-Key 头，故用 HMAC 短时令牌走
  query string。secret = settings.API_KEY；未配置（本地开发）时与
  require_api_key 一致：直接放行、不校验令牌。
"""
import hashlib
import hmac
import os
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from app.config import settings

# 后缀 → (mime, kind)。kind 必须与 material_type(video/image) 对齐。
_VIDEO = {
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".mov": "video/quicktime", ".m4v": "video/x-m4v",
}
_IMAGE = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
}
_CHUNK = 1 << 20  # 1MB


def _media_root() -> Path:
    root = Path(settings.MEDIA_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def kind_for_ext(ext: str) -> Optional[str]:
    ext = ext.lower()
    if ext in _VIDEO:
        return "video"
    if ext in _IMAGE:
        return "image"
    return None


async def save_upload(file: UploadFile) -> dict:
    """校验 + 流式落盘。返回 {file_path, file_name, file_size, mime_type, kind}。
    超限 → 413 并删除半截文件；非法类型 → 400。"""
    orig = (file.filename or "").strip()
    ext = os.path.splitext(orig)[1].lower()
    mime = _VIDEO.get(ext) or _IMAGE.get(ext)
    kind = kind_for_ext(ext)
    if not mime or not kind:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件类型 {ext or '(无后缀)'}；仅支持视频 "
                   f"{sorted(_VIDEO)} 与图片 {sorted(_IMAGE)}",
        )
    root = _media_root()
    rel = f"{uuid.uuid4().hex}{ext}"
    dest = root / rel
    size = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = await file.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.MEDIA_MAX_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"文件超过上限 {settings.MEDIA_MAX_BYTES // (1024*1024)}MB",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="空文件")
    return {"file_path": rel, "file_name": orig or rel,
            "file_size": size, "mime_type": mime, "kind": kind}


def resolve(file_path: str) -> Path:
    """把 DB 里的相对路径解析成 MEDIA_ROOT 下的绝对路径，并防路径穿越。"""
    root = _media_root().resolve()
    p = (root / file_path).resolve()
    if root not in p.parents and p != root:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="非法路径")
    return p


def delete_file(file_path: Optional[str]) -> None:
    if not file_path:
        return
    try:
        resolve(file_path).unlink(missing_ok=True)
    except Exception:
        pass  # 删档失败不应阻断 DB 记录删除


def _secret() -> str:
    return settings.API_KEY or ""


def sign(material_id: int, ttl: Optional[int] = None) -> str:
    exp = int(time.time()) + (ttl or settings.MEDIA_URL_TTL_SECONDS)
    mac = hmac.new(_secret().encode(), f"{material_id}:{exp}".encode(),
                   hashlib.sha256).hexdigest()[:16]
    return f"{exp}.{mac}"


def verify(material_id: int, token: Optional[str]) -> bool:
    # 与 require_api_key 一致：未配置 API_KEY（本地开发）则放行。
    if not settings.API_KEY:
        return True
    if not token or "." not in token:
        return False
    exp_s, mac = token.split(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < time.time():
        return False
    expected = hmac.new(_secret().encode(), f"{material_id}:{exp}".encode(),
                        hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(mac, expected)


def stream_url(material) -> Optional[str]:
    """upload 素材 → 带签名令牌的站内播放 URL；link 素材 → None。"""
    if getattr(material, "source", "link") != "upload" or not material.file_path:
        return None
    return f"/api/materials/{material.id}/file?token={sign(material.id)}"


# ── 通用文件流式响应（支持 Range，供站内 <video>/<img> 预览）──────────────
# 与 routers/materials.py 的手写实现同一口径；抽到这里供产品素材文件路由复用，
# 避免再抄一份 Range 解析。materials 路由暂保留自有实现（不动已上线代码）。

_STREAM_CHUNK = 1 << 18  # 256KB


def _file_iter(path: Path, start: int, end: int):
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


def stream_file(path: Path, mime: str, file_name: Optional[str],
                range_header: Optional[str]) -> StreamingResponse:
    """流式响应一个本地文件，支持 HTTP Range（视频可拖拽进度条）。

    Content-Disposition 按 RFC 6266：ASCII 兜底 + filename*=UTF-8'' 带真实名，
    避免中文文件名让响应头 latin-1 编码 500（与 materials 路由同坑同解）。
    """
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件已丢失")
    size = path.stat().st_size
    mime = mime or "application/octet-stream"
    raw_name = file_name or path.name
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
