from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict

from app.schemas.tag import MaterialTagValueItem, MaterialTagValueInput


class MaterialCreate(BaseModel):
    """外链素材。上传素材走 POST /api/materials/upload（multipart），不走这里。"""
    app_id: str
    title: str
    url: str
    platform: Optional[str] = None
    material_type: str = "video"
    tags: list[str] = []
    notes: Optional[str] = None
    # 结构化标签（P2）：随建素材一并打。required 维度缺失会被拒（400）。
    tag_values: list[MaterialTagValueInput] = []


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


class MaterialScene(BaseModel):
    ts: float  # 秒（精度 0.1s 即可）
    description: str


class MaterialHook(BaseModel):
    ts: float
    kind: str  # 卸负 / CTA / 反转 / 情绪高潮 / 价值主张 …
    note: str


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

    # LLM 视频分析结果（None 表示尚未分析或无）
    analysis_status: Optional[str] = None  # pending/running/done/failed
    analysis_brief: Optional[str] = None
    analysis_tags: Optional[list[str]] = None
    analysis_scenes: Optional[list[MaterialScene]] = None
    analysis_hooks: Optional[list[MaterialHook]] = None
    analyzed_at: Optional[datetime] = None
    analysis_model: Optional[str] = None
    analysis_cost_usd: Optional[float] = None
    analysis_error: Optional[str] = None
    # 抽帧 + 联系单：DB 只存元信息，URL 由 service 注入（含 HMAC 短时令牌）
    analysis_frames: Optional[list[dict]] = None  # [{ts, url}]
    analysis_contact_sheet_url: Optional[str] = None

    # 结构化标签（P2）：素材在各一级标签维度下已打的值；由路由批量注入。
    tag_values: list[MaterialTagValueItem] = []
