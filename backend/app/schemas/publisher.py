from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from datetime import datetime
from typing import Literal, Optional

from app.services.provenance import SOURCE_TYPES


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


class PublisherItunesArtistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    artist_id: str
    platform: str = "ios"
    label: Optional[str] = None
    last_synced_at: Optional[datetime] = None


class PublisherItunesArtistCreate(BaseModel):
    artist_id: str
    # 'ios' = iTunes artistId（必须纯数字）；'gp' = Google Play 开发者页 id
    # （developer?id= 的名称型或 dev?id= 的数字型均可）。
    platform: Literal["ios", "gp"] = "ios"
    label: Optional[str] = None

    @model_validator(mode="after")
    def _valid_artist_id(self) -> "PublisherItunesArtistCreate":
        v = self.artist_id.strip()
        if not v:
            raise ValueError("artist_id must not be empty")
        if self.platform == "ios" and not v.isdigit():
            raise ValueError("artist_id must be the numeric iTunes artistId, e.g. 1717022676")
        if len(v) > 30:
            raise ValueError("artist_id too long (max 30 chars)")
        self.artist_id = v
        return self


class PublisherSourceOut(BaseModel):
    id: int
    url: str
    title: Optional[str] = None
    source_type: str
    is_primary: bool = False  # 派生：source_type 是否一手（见 services/provenance）
    confidence: Optional[str] = None
    as_of: Optional[str] = None
    note: Optional[str] = None


class PublisherSourceCreate(BaseModel):
    url: str
    title: Optional[str] = None
    source_type: str
    confidence: Optional[str] = None
    as_of: Optional[str] = None
    note: Optional[str] = None

    @field_validator("source_type")
    @classmethod
    def _valid_source_type(cls, v: str) -> str:
        if v not in SOURCE_TYPES:
            raise ValueError(f"source_type must be one of {SOURCE_TYPES}")
        return v


# 主体间股权/母子关系类型
RELATION_TYPES = ("wholly_owned", "controlling", "minority", "affiliate")


class PublisherRelationCreate(BaseModel):
    """从某主体视角新增一条股权关系。

    counterpart_role 表达「对方相对本主体的角色」：
    - 'parent' → 对方是本主体的母公司/投资方（parent=对方, child=本主体）
    - 'child'  → 对方是本主体的子公司/被投（parent=本主体, child=对方）
    """
    counterpart_id: int
    counterpart_role: str
    relation_type: str
    stake_pct: Optional[float] = None
    note: Optional[str] = None

    @field_validator("counterpart_role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        if v not in ("parent", "child"):
            raise ValueError("counterpart_role must be 'parent' or 'child'")
        return v

    @field_validator("relation_type")
    @classmethod
    def _valid_relation_type(cls, v: str) -> str:
        if v not in RELATION_TYPES:
            raise ValueError(f"relation_type must be one of {RELATION_TYPES}")
        return v

    @field_validator("stake_pct")
    @classmethod
    def _valid_stake(cls, v):
        if v is not None and not (0 <= v <= 100):
            raise ValueError("stake_pct must be between 0 and 100")
        return v


class PublisherRelationLinkOut(BaseModel):
    """从某主体视角看到的一条关系链接（对方主体名已解析）。"""
    relation_id: int
    entity_id: int  # 对方主体 id
    name: str       # 对方主体名
    relation_type: str
    stake_pct: Optional[float] = None
    note: Optional[str] = None


class PublisherTopProductOut(BaseModel):
    """折叠行的产品图标锚点：只要 app_id / 名字 / icon，用于一眼认主体。"""
    app_id: str
    name: Optional[str] = None
    icon_url: Optional[str] = None


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
    itunes_artists: list[PublisherItunesArtistOut] = []
    sources: list[PublisherSourceOut] = []
    # 溯源档位：primary(有一手源) / secondary(仅二手) / none(未溯源)。见 services/provenance。
    provenance_tier: str = "none"
    parents: list[PublisherRelationLinkOut] = []   # 本主体的母公司/投资方
    children: list[PublisherRelationLinkOut] = []  # 本主体的子公司/关联
    product_count: Optional[int] = None  # 旗下产品数；列表视图按需填，详情视图必填
    # 折叠态产品图标锚点：旗下产品按收入降序的前 3 个（icon 来自 game_rankings，零 ST 配额）。
    top_products: list[PublisherTopProductOut] = []
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
