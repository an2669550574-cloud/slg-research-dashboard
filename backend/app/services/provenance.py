"""厂商主体溯源分级（provenance）：来源类型 → 一手/二手，决定调研沉淀的可信度档位。

沿用 vietnam-market-intel 实体溯源那套纪律：对「谁研发 / 谁发行 / 谁持股某主体」这类
**归属型断言**，受控域名 / 商店开发者字段 / 工商登记 / 官方备案算一手；媒体 / 百科 /
分析 / 厂商自述只能作二手佐证。查不到一手源就老实标 unverified，绝不臆测。
"""

# 一手源（primary）：身份归属可直接采信的来源
PRIMARY_SOURCE_TYPES: tuple[str, ...] = (
    "registry",           # 工商登记（企查查 / 天眼查 / masothue 等）
    "official_filing",    # 官方备案（SEC / 港交所 / 版号等）
    "official_platform",  # 应用商店开发者字段（App Store / Google Play 主体）
    "official_domain",    # 厂商受控域名官网 / 隐私政策 / ToS
)

# 二手源（secondary / medium）：可作佐证，归属类不能仅靠它
SECONDARY_SOURCE_TYPES: tuple[str, ...] = (
    "media",        # 行业媒体报道
    "reference",    # 维基 / 百科
    "analysis",     # 分析 / 观点
    "self_report",  # 厂商营销自述
)

SOURCE_TYPES: tuple[str, ...] = PRIMARY_SOURCE_TYPES + SECONDARY_SOURCE_TYPES
_PRIMARY_SET = frozenset(PRIMARY_SOURCE_TYPES)


def is_primary(source_type: str) -> bool:
    return source_type in _PRIMARY_SET


def provenance_tier(source_types) -> str:
    """主体溯源档位：有 ≥1 一手源 → 'primary'；仅二手 → 'secondary'；无来源 → 'none'。

    驱动档案上的溯源徽标（已溯源·一手 / 仅二手 / 未溯源），把「哪些主体归属站得住、
    哪些还得补一手源」一眼显出来。
    """
    types = list(source_types)
    if any(t in _PRIMARY_SET for t in types):
        return "primary"
    if types:
        return "secondary"
    return "none"
