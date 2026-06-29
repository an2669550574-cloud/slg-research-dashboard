from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class OwnProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    brief: str = Field(..., min_length=1, max_length=4000)
    # 「同赛道」匹配关键词（逗号分隔题材词）。题材太宽泛，优先用 match_subgenre。空 = 不参与。
    match_keywords: Optional[str] = Field(None, max_length=500)
    # 「同赛道」目标玩法子品类（受控词表，如「数字门SLG」）。配了就按子品类精确匹配（优先于关键词）。
    match_subgenre: Optional[str] = Field(None, max_length=100)
    is_default: bool = False


class OwnProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    brief: Optional[str] = Field(None, min_length=1, max_length=4000)
    match_keywords: Optional[str] = Field(None, max_length=500)
    match_subgenre: Optional[str] = Field(None, max_length=100)
    is_default: Optional[bool] = None


class OwnProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    brief: str
    match_keywords: Optional[str] = None
    match_subgenre: Optional[str] = None
    is_default: bool
    created_at: datetime
    updated_at: datetime


# ── 自有产品素材 ─────────────────────────────────────────────────────────

class OwnProductMaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    own_product_id: int
    asset_type: str  # video / image / text
    title: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    text_content: Optional[str] = None
    created_at: datetime
    # 上传素材的站内预览 URL（HMAC 令牌）；text 素材为 None
    preview_url: Optional[str] = None


class OwnProductMaterialTextCreate(BaseModel):
    """纯文字素材（商店描述 / 介绍）。视频 / 图片走 multipart 上传端点。"""
    title: Optional[str] = Field(None, max_length=300)
    text_content: str = Field(..., min_length=1, max_length=20000)


class OwnProductAnalyzeResult(BaseModel):
    """AI 反推的产品画像。brief 是拼好的成稿，可直接填进产品 brief 文本框。"""
    brief: str
    theme: Optional[str] = None            # 题材
    gameplay: Optional[str] = None         # 玩法
    selling_points: Optional[list[str]] = None  # 卖点
    audience: Optional[str] = None         # 目标受众
    differentiation: Optional[str] = None  # 差异化
    cost_usd: float
    model: str
    material_count: int
