from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class GameCreate(BaseModel):
    """创建游戏。name/publisher/icon_url/release_date/description 缺失时，iOS 数字 app_id 会自动从 iTunes 补全。"""
    app_id: str
    name: Optional[str] = None
    publisher: Optional[str] = None
    icon_url: Optional[str] = None
    platform: str = "ios"
    country: str = "US"
    release_date: Optional[str] = None
    description: Optional[str] = None
    tags: list[str] = []


class GameUpdate(BaseModel):
    """更新游戏元信息。所有字段可选，仅传入的字段会被覆盖。app_id 不能改。"""
    name: Optional[str] = None
    publisher: Optional[str] = None
    icon_url: Optional[str] = None
    platform: Optional[str] = None
    country: Optional[str] = None
    release_date: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None


class GameOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    app_id: str
    name: str
    publisher: Optional[str] = None
    icon_url: Optional[str] = None
    category: str = "SLG"
    platform: str = "ios"
    country: str = "US"
    release_date: Optional[str] = None
    description: Optional[str] = None
    tags: list[str] = []
    created_at: datetime
    updated_at: datetime


class GameRankingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    app_id: str
    date: str
    rank: Optional[int] = None
    downloads: Optional[float] = None
    revenue: Optional[float] = None
    country: str = "US"
    platform: str = "ios"


class RankingTodayOut(BaseModel):
    """Sensor Tower 今日榜单聚合返回项。来源既可能是数据库也可能是外部 API，因此不绑定 ORM。"""
    app_id: str
    name: Optional[str] = None
    publisher: Optional[str] = None
    icon_url: Optional[str] = None
    rank: Optional[int] = None
    downloads: Optional[float] = None
    revenue: Optional[float] = None
    date: Optional[str] = None


class TrendPoint(BaseModel):
    date: str
    value: Optional[float] = None
    rank: Optional[int] = None


class MetricsOut(BaseModel):
    rankings: list[TrendPoint] = []
    downloads: list[TrendPoint] = []
    revenue: list[TrendPoint] = []
