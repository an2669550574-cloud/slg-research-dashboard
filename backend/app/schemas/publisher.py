from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional


class PublisherAliasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    keyword: str
    label: Optional[str] = None


class PublisherAliasCreate(BaseModel):
    keyword: str
    label: Optional[str] = None


class PublisherAppIdOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    app_id: str
    note: Optional[str] = None


class PublisherAppIdCreate(BaseModel):
    app_id: str
    note: Optional[str] = None


class PublisherEntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    name_en: Optional[str] = None
    hq_region: Optional[str] = None
    is_slg: bool
    brief: Optional[str] = None
    sort_order: int
    aliases: list[PublisherAliasOut] = []
    app_ids: list[PublisherAppIdOut] = []
    product_count: Optional[int] = None  # 旗下产品数；列表视图按需填，详情视图必填
    created_at: datetime
    updated_at: datetime


class PublisherEntityCreate(BaseModel):
    name: str
    name_en: Optional[str] = None
    hq_region: Optional[str] = None
    is_slg: bool = True
    brief: Optional[str] = None
    sort_order: int = 0
    # 建主体时可一并带初始马甲 / app_id；后续增删走子资源端点。
    aliases: list[PublisherAliasCreate] = []
    app_ids: list[PublisherAppIdCreate] = []


class PublisherEntityUpdate(BaseModel):
    name: Optional[str] = None
    name_en: Optional[str] = None
    hq_region: Optional[str] = None
    is_slg: Optional[bool] = None
    brief: Optional[str] = None
    sort_order: Optional[int] = None


class PublisherProductOut(BaseModel):
    """主体旗下某产品的聚合行：跨已监测市场窗口内合计下载/收入，零 ST 配额、本地库出。"""
    app_id: str
    name: Optional[str] = None
    publisher: Optional[str] = None
    icon_url: Optional[str] = None
    downloads: int = 0
    revenue: float = 0
    matched_by: str  # "alias" | "app_id" —— 该产品因何归属本主体
