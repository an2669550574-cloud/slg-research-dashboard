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


class RegionReleaseOut(BaseModel):
    """tracked iOS 竞品某 storefront 的上架日（需求② 子项③ / ADR 0004）。"""
    model_config = ConfigDict(from_attributes=True)

    country: str
    release_date: Optional[str] = None  # NULL = 该区查不到 / 未上架


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
    # 发行商命中 SLG 白名单。默认 True = fail-open：某条没显式打标也不会被
    # 「仅 SLG」默认视图静默隐藏（符合本项目「绝不静默丢数据」原则）。
    is_slg: bool = True


class TrendPoint(BaseModel):
    date: str
    value: Optional[float] = None
    rank: Optional[int] = None


class MetricsOut(BaseModel):
    rankings: list[TrendPoint] = []
    downloads: list[TrendPoint] = []
    revenue: list[TrendPoint] = []


class MetricsCoverage(BaseModel):
    """某 app 在本地 game_rankings 里实际有数据的 (国家, platform) 组合。
    前端据此渲染国家/平台切换并默认选数据最全的那个。"""
    country: str
    platform: str
    days: int        # 该组合总行数（含 rank=NULL 的历史销量回填行）
    sales_days: int  # 有下载/收入的天数 —— 决定收入/下载图是否画得出
    rank_days: int   # 有 rank 的天数 —— 决定排名走势图密度


class AggregateLeaderboardOut(BaseModel):
    """仪表盘「合计·区间」视图：跨该 app 全部已监测市场在窗口内合计销量
    后的榜单行。与详情页头部 `已监测市场合计` 同口径，数字可直接对账。"""
    app_id: str
    name: Optional[str] = None
    publisher: Optional[str] = None
    icon_url: Optional[str] = None
    downloads: int = 0
    revenue: float = 0.0
