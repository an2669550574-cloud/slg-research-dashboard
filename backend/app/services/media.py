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

from fastapi import HTTPException, UploadFile, status

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
