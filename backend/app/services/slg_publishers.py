"""SLG 发行商白名单。

应用商店最细只到「策略」大类（iOS 7017 / Android game_strategy），没有
「SLG」子类，所以策略畅销榜天然混进棋牌 / 塔防 / 卡牌策略等非 SLG 产品。
这里按发行商名做**后置过滤**——零额外配额（榜单已全量拉回，只是给每行
打 is_slg 标记，前端可切「仅 SLG / 全部策略」）。

维护方式：前端切到「全部策略」，看混进来的是哪些发行商；确属 SLG 专精
的厂商，把它的关键词加进 SLG_PUBLISHER_KEYWORDS 即可。匹配是「小写分词
后的连续 token 子序列」，对 `Pte. Ltd.` / `Inc` / `.COM` / 标点等公司
后缀天然鲁棒，不用自己清洗。
"""
import re

# 关键词 = 小写分词后的连续 token 串，命中即视为 SLG 竞品发行商。
# 故意只收「SLG 专精」厂商：Supercell / Scopely / Dream Games 等虽然
# 也在策略畅销榜，但非 SLG 口径（COC/大富翁/消除），不收。
SLG_PUBLISHER_KEYWORDS: tuple[str, ...] = (
    "century games",      # Whiteout Survival, Kingshot
    "diandian",           # 点点互动
    "first fun",          # 部分地区 Whiteout 发行标
    "firstfun",
    "funplus",            # State of Survival, Sea of Conquest, Stormshot
    "kingsgroup",         # King of Avalon, Guns of Glory（FunPlus 系）
    "igg",                # Lords Mobile, Viking Rise
    "lilith",             # Rise of Kingdoms, Call of Dragons, Warpath
    "elex",               # Clash of Kings
    "camel games",        # Age of Origins / Age of Z
    "tap4fun",            # Kiss of War, Invasion
    "topwar",             # Top War
    "top war",
    "37 mobile games",    # Puzzles & Survival
    "37games",
    "37 interactive",
    "top games",          # Evony（Top Games Inc）
    "onemt",              # Kingdom Guard 等
    "tilting point",      # 多款 SLG 联运
    "machine zone",       # Game of War, Mobile Strike
    "stillfront",         # Conflict of Nations, Supremacy
    "innogames",          # Forge of Empires, Tribal Wars
    "joycity",            # Gunship Battle: Total Warfare
)

_ALLOW: tuple[tuple[str, ...], ...] = tuple(tuple(k.split()) for k in SLG_PUBLISHER_KEYWORDS)


def _tokens(s: str | None) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split()


def is_slg_publisher(publisher: str | None) -> bool:
    """publisher 的 token 序列里出现任一白名单关键词（作为连续子序列）即 True。

    publisher 为空（如 Android 富化失败没抓到发行商）→ False：宁可在
    「仅 SLG」视图漏掉、让用户切「全部策略」补看，也不污染默认视图。
    """
    toks = _tokens(publisher)
    if not toks:
        return False
    for kw in _ALLOW:
        n = len(kw)
        for i in range(len(toks) - n + 1):
            if tuple(toks[i:i + n]) == kw:
                return True
    return False
