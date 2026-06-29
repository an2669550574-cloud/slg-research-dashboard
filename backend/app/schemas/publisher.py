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
    # 旗下产品在「各市场最新快照」里的最佳（最小）畅销榜名次 + 命中市场（如 "JP/android"）。
    # 用于「按畅销榜名次」排序，让畅销头部公司排最前；无上榜产品（纯控股母体/软启动）则为空。
    best_rank: Optional[int] = None
    best_rank_market: Optional[str] = None
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
    matched_by: str  # "alias" | "app_id" | "radar" —— 该产品因何归属本主体
    genre: Optional[str] = None  # 雷达(radar)产品的子品类，无榜单 publisher 时作副标题兜底


class PublisherHealthOut(BaseModel):
    """主体模块的数据健康度快照——把「这两轮手写的 audit 脚本」固化成端点。

    驱动看板自检：
    - 完整溯源占比 (tier_primary / total)
    - 待补 backlog（无 brief / 无源 / 仅二手 / 无关系且非孤厂等）
    - 命名 backlog（国内厂未中文化）
    - 复核 backlog（>12 个月没核验的有源主体）

    端点零 ST 配额、纯本地 DB；前端可调，也可 curl 出 CSV/周报。
    """
    total: int                       # 主体总数
    tier_primary: int                # 有 ≥1 一手源
    tier_secondary: int              # 仅二手源
    tier_none: int                   # 无任何源
    empty_brief: int                 # brief 严格为空（null / "" / 只含空白）
    no_sources: int                  # 一条源都没挂
    no_primary_source: int           # 至少有源但全二手
    no_relations: int                # parents+children=0（独立厂 OR 待挂集团）
    no_aliases_no_appids: int        # 既无 alias 又无 app_id（极端裸壳）
    cn_no_chinese_name: int          # hq=国内 但 name 全英文
    stale_review: int                # 有源、最新 as_of ≥ 12 个月（建议复核）
    total_aliases: int
    total_app_ids: int
    total_sources: int
    total_relations: int
    total_itunes_artists: int = 0          # 已 wire 的 iOS 开发者账号（雷达自动召回器）总数
    entities_without_itunes_artist: int = 0  # 没接 iOS 雷达的主体数（含资本方/app_id 钉的大厂，故偏高）
    capital_entities: int            # is_slg=False 的资本方/控股母体
    avg_brief_len: int               # 平均 brief 字符数
    max_brief_len: int               # 单主体最长 brief（已加戳记后会拉高）


class PublisherGapOut(BaseModel):
    """调研缺口行：近 N 天有收入、任何 alias/app_id 都没命中的 publisher。

    驱动「未归属高收入发行商」提示——把 PUBLISHERS.md 里「数据驱动找缺口」从手 SQL
    抬进 UI，进页面就看见。点「建主体」预填 publisher 名为初始 alias，省得手敲。
    """
    publisher: str           # 榜单原始 publisher 字符串（用作 alias keyword 起点）
    revenue: float           # 窗口内累计收入分（across all apps under this publisher）
    downloads: int           # 窗口内累计下载
    app_count: int           # 该 publisher 名下涉及多少 app_id
    top_app: PublisherTopProductOut  # 收入最高的代表 app（icon + 名）


class PublisherDownloadLeadOut(BaseModel):
    """下载榜早期信号行：下载榜(免费榜) is_slg=false（白名单未收录）但 genre=Strategy 的新品。

    grossing 缺口（PublisherGapOut）是「已起量、该建档的发行商」；这个是更早的信号——
    新厂常先软启动、买量起量先反映在下载榜装机量，几个月后才进收入榜。把 digest 方案①
    的「待建档新厂线索」搬进 publishers 页 UI，让维护者随时浏览这条早期 backlog（不止
    digest 推一次）。数据源 = market_newcomer_log（免费富化 genre/summary_cn，零 ST）。
    """
    app_id: str
    name: str
    publisher: Optional[str] = None
    genre: Optional[str] = None            # 英文 genre（前端可转中文）
    summary_cn: Optional[str] = None       # 一句话中文摘要（#147 已扩到 is_slg=false 待识别新厂）
    icon_url: Optional[str] = None
    store_url: Optional[str] = None
    country: str
    platform: str
    rank: Optional[int] = None
    first_detected_at: Optional[str] = None  # ISO，检出时间（最新在前）


class PublisherArtistSuggestionOut(BaseModel):
    """雷达覆盖建议行：未接 iOS 雷达的 is_slg 主体，从其已钉 iOS app_id 反解出的开发者账号候选。

    驱动「📡 雷达覆盖建议」面板——把「手动找开发者页 → 抄 artistId → 粘进抽屉」这段 toil
    自动化成「一眼看 entity→artistName 对不对 → 一键接入」。零 ST 配额（免费 iTunes lookup）。
    """
    entity_id: int
    entity_name: str
    source_app_id: str             # 反解所用的 iOS 数字 app_id（主体已钉）
    source_app_name: Optional[str] = None  # 该 app 名（供人工核对账号归属）
    artist_id: str                 # 反解出的开发者账号 artistId（接入后即雷达账号）
    artist_name: Optional[str] = None  # 开发者账号名（人工把关锚点：与主体对得上才接）


class PublisherIgnoreOut(BaseModel):
    """缺口忽略名单条目：被人工标为「非 SLG 主体」的发行商 / app，不再进缺口提示。"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: Literal["publisher", "app_id"]
    value: str               # publisher: corp_squash 归一键；app_id: 原始 app_id
    label: Optional[str] = None  # 展示用原始名（建条目时传入的 publisher 串 / app 名）
    note: Optional[str] = None
    created_at: datetime


class PublisherIgnoreCreate(BaseModel):
    """新增忽略：raw_value 传原始串（publisher 名或 app_id）——publisher 粒度由后端
    归一成 corp_squash 键存储，原始串落到 label 供展示。"""
    kind: Literal["publisher", "app_id"]
    raw_value: str           # 原始 publisher 名 或 app_id
    label: Optional[str] = None
    note: Optional[str] = None

    @field_validator("raw_value")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("raw_value 不能为空")
        return v.strip()
