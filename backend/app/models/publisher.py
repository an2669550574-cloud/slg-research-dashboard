from sqlalchemy import String, Integer, Float, DateTime, Text, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional
from app.database import Base, utcnow_naive


class PublisherEntity(Base):
    """厂商主体：一个真实发行/研发实体（如「江娱互动」「FunPlus」）。

    一个主体在海外常用多个发行马甲（PublisherAlias）发行产品；旗下多品类大厂的
    个别真·SLG 单品按 app_id 精确钉（PublisherAppId）。「主体→旗下产品」是
    **查询态聚合**——用 aliases 对 game_rankings.publisher 做 token 子序列匹配 +
    app_ids 精确匹配，不在 game_rankings 上加外键，对榜单数据零污染、零迁移。

    is_slg 标记同时驱动榜单「仅 SLG / 全部策略」过滤与竞品异动检测——本表 +
    aliases + app_ids 是 is_slg 判定的唯一数据源（运行时走 slg_publishers 内存索引）。
    """
    __tablename__ = "publisher_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))  # 中文主体名，如「江娱互动」
    name_en: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    hq_region: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 国内 / 海外 / 具体国家
    is_slg: Mapped[bool] = mapped_column(Boolean, default=True)
    brief: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 短背景 / 调研备注
    # 资本集团报表名（迁移 0045）。成员名单由 publisher_relations 推导、不落库，本列只存
    # 组名——根主体名常不是报表要的叫法（根「元趣娱乐」→ 报表「元趣系」）。见 publisher_groups。
    group_label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class PublisherAlias(Base):
    """主体的海外发行马甲：榜单 publisher 字段里出现的发行标。

    keyword = 小写分词后的**连续 token 子序列**，命中 game_rankings.publisher 即
    归一到该主体（与旧 slg_publishers 的匹配同规则，对 `Pte. Ltd.`/`Inc`/标点鲁棒）。
    一个主体可有多条（如 FunPlus 系 = funplus / kingsgroup）。label 仅供展示。
    """
    __tablename__ = "publisher_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("publisher_entities.id", ondelete="CASCADE"), index=True
    )
    keyword: Mapped[str] = mapped_column(String(100))  # token 匹配键，如 "kingsgroup"
    label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # 展示名，如 "KingsGroup"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class PublisherAppId(Base):
    """多品类大厂旗下单款真·SLG 的精确钉：按发行商收会连带一堆非 SLG，只能按
    app_id 精确归到主体。跨商店 iOS 数字 id 与 Android 包名各算一行（两端 id 不同）。
    """
    __tablename__ = "publisher_app_ids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("publisher_entities.id", ondelete="CASCADE"), index=True
    )
    app_id: Mapped[str] = mapped_column(String(100), index=True)
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class PublisherSource(Base):
    """主体调研出处（溯源）：一条支撑「该主体身份 / 归属 / 股权」判断的来源。

    source_type 决定一手/二手分级（见 services/provenance），是「调研沉淀可信度」的依据：
    工商登记 / 官方备案 / 商店开发者字段 / 受控域名 = 一手；媒体 / 百科 / 分析 / 厂商
    自述 = 二手。让档案能沉淀得住、可回溯，而不是只有一段自由文本 brief。
    """
    __tablename__ = "publisher_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("publisher_entities.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(1000))
    title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # provenance.SOURCE_TYPES 之一（registry / official_filing / official_platform /
    # official_domain / media / reference / analysis / self_report）
    source_type: Mapped[str] = mapped_column(String(50))
    # high / medium / low / unverified（自由填，查不到一手源就标 unverified）
    confidence: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    as_of: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 核验日期 YYYY-MM-DD
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class PublisherItunesArtist(Base):
    """主体的 App Store 开发者账号（iTunes artistId）。

    一个主体可有多个开发者账号（如元趣系 = First Fun HK 与 FUNFLY PTE. LTD. 两个
    账号）。artist_id 全局唯一——一个开发者账号只归属一个主体。
    iTunes lookup API（免费、非 Sensor Tower）按 artistId 拉账号下全部 app 清单，
    周级 diff 出"新上架"——不依赖产品进榜，软启动期即可抓到。
    """
    __tablename__ = "publisher_itunes_artists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("publisher_entities.id", ondelete="CASCADE"), index=True
    )
    artist_id: Mapped[str] = mapped_column(String(255), unique=True)  # iOS 数字型如 "1717022676"；GP 可为名称型长 id（如 "SINGAPORE JUST GAME TECHNOLOGY PTE. LTD."）
    # 'ios' = iTunes artistId；'gp' = Google Play 开发者页 id（名称型如 "GAME SPARK"
    # 或数字型）。GP 侧复用同一套清单 diff/基线语义，apps 行 storefronts 固定 'gp'。
    platform: Mapped[str] = mapped_column(String(10), default="ios", server_default="ios")
    label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # 如 "River Game HK Limited"
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class PublisherItunesApp(Base):
    """开发者账号下见过的 app 清单快照——「App Store 新上架」diff 的基线与结果。

    首次同步某账号时全量落库并标 is_baseline=True（无从判断"新"，与 newcomers 的
    no_baseline 同语义）；此后出现的新 track_id 落库为 is_baseline=False = 新上架。
    """
    __tablename__ = "publisher_itunes_apps"
    __table_args__ = (
        UniqueConstraint("artist_row_id", "track_id", name="uq_itunes_app_per_artist"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("publisher_entities.id", ondelete="CASCADE"), index=True
    )
    artist_row_id: Mapped[int] = mapped_column(
        ForeignKey("publisher_itunes_artists.id", ondelete="CASCADE"), index=True
    )
    track_id: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(300))
    bundle_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    release_date: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # ISO 日期
    track_view_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    # 以下展示字段全部来自 fetch_artist_apps 那一次免费 iTunes lookup 的同一响应——
    # 零增量 ST 配额。genre 取 genres[] 里第一个非 "Games" 的子品类（Strategy/Puzzle…）。
    artwork_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    genre: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # averageUserRating 0-5
    rating_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # formattedPrice，如 "Free"
    # 该 app 在哪些 storefront 可见（逗号小写，如 "ph,ca"）。每轮同步取并集刷新——
    # 「PH/CA 可见、us 不在列」= 软启动中；后补上 us = 扩区/全球上线（触发扩区提示）。
    storefronts: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # 检出详情（同一免费 lookup 响应）：描述截断 / 截图 URL JSON 数组（≤5）/ 支持语言码。
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    screenshot_urls: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    languages: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class PublisherIgnore(Base):
    """缺口忽略名单：在 `/gaps`（未归属高收入发行商）里手动标「这不是要建档的 SLG
    主体」的条目，从此不再出现在缺口提示里。

    缺口稳态被 ~17 个已知非 SLG 巨头（Niantic / Supercell / EA / Chess.com /
    KONAMI 等）刷屏 → banner-blind（#84 因此整块下线 UI）。忽略名单把这些一次性
    剔掉，让缺口收敛到 2~3 个真正可操作信号，UI 才值得抬回。

    两种粒度（kind）：
    - `publisher`：忽略整个发行商。value 存 **name_match.corp_squash 归一键**
      （去法人后缀拼接），让 "Niantic, Inc." 与 "Niantic Inc" 折叠到同一条。
    - `app_id`：只忽略某一款 app（同发行商其它 app 仍可能是 SLG，单品剔除）。
      value 存原始 app_id。

    纯本地库、零 ST 配额；与 is_slg 判定**完全无关**（只影响缺口提示，不动榜单过滤）。
    """
    __tablename__ = "publisher_ignores"
    __table_args__ = (
        UniqueConstraint("kind", "value", name="uq_publisher_ignore"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # 'publisher' | 'app_id'
    value: Mapped[str] = mapped_column(String(200), index=True)  # squash 键 / app_id
    label: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)  # 展示用原始名
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class PublisherRelation(Base):
    """主体间股权/母子关系：parent_id（母公司/投资方）→ child_id（子公司/被投）。

    有向边，relation_type 描述强度（全资 / 控股 / 参股 / 关联），stake_pct 选填持股
    百分比。UI 在某主体卡片上从两侧看：作为 child → 列它的母公司；作为 parent → 列它的
    子公司/关联。(parent_id, child_id) 唯一，禁自环（应用层校验）。沿用「提示为主、人工
    把关」：关系也应挂溯源源佐证（在调研溯源区登记），但本表不做硬绑定。
    """
    __tablename__ = "publisher_relations"
    __table_args__ = (
        UniqueConstraint("parent_id", "child_id", name="uq_publisher_relation_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int] = mapped_column(
        ForeignKey("publisher_entities.id", ondelete="CASCADE"), index=True
    )
    child_id: Mapped[int] = mapped_column(
        ForeignKey("publisher_entities.id", ondelete="CASCADE"), index=True
    )
    # wholly_owned(全资) / controlling(控股) / minority(参股) / affiliate(关联)
    relation_type: Mapped[str] = mapped_column(String(30))
    stake_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 持股 %，0-100
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
