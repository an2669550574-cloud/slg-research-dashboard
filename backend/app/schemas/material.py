from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class MaterialCreate(BaseModel):
    """外链素材。上传素材走 POST /api/materials/upload（multipart），不走这里。"""
    app_id: str
    title: str
    url: str
    platform: Optional[str] = None
    material_type: str = "video"
    tags: list[str] = []
    notes: Optional[str] = None


class MaterialUpdate(BaseModel):
    # app_id 可改：用于给已有素材重新归类到游戏（空串 = 取消关联）。
    app_id: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    platform: Optional[str] = None
    material_type: Optional[str] = None
    tags: Optional[list[str]] = None
    notes: Optional[str] = None


class MaterialTagCount(BaseModel):
    tag: str
    count: int


class MaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    app_id: str
    title: str
    url: Optional[str] = None
    source: str = "link"  # link / upload
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    # upload 素材的站内播放/预览签名 URL（含短时令牌）；link 素材为 None
    stream_url: Optional[str] = None
    platform: Optional[str] = None
    material_type: str = "video"
    tags: list[str] = []
    notes: Optional[str] = None
    created_at: datetime
