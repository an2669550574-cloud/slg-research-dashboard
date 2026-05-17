"""SLG 竞品判定：发行商白名单 + app_id 关注名单（两路 OR）。

应用商店最细只到「策略」大类（iOS 7017 / Android game_strategy），没有
「SLG」子类，所以策略畅销榜天然混进棋牌 / 塔防 / 卡牌策略等非 SLG 产品。
这里做**后置过滤**——零额外配额（榜单已全量拉回，只给每行打 is_slg
标记，前端可切「仅 SLG / 全部策略」）。

两路判定：
1. SLG_PUBLISHER_KEYWORDS —— SLG 专精发行商，覆盖其全部产品。
2. SLG_APP_IDS —— 多品类大厂（Warner Bros / Scopely / Level Infinite /
   Plarium / Farlight）旗下的个别真·SLG。按发行商收会连带一堆非 SLG，
   只能按 app_id 精确钉。

维护方式：前端切「全部策略」看漏网的——是 SLG 专精厂商就加关键词；
是多品类大厂的单款 SLG 就把 app_id 加进关注名单。发行商匹配是「小写
分词后的连续 token 子序列」，对 `Pte. Ltd.` / `Inc` / `.COM` / 标点等
公司后缀天然鲁棒，不用自己清洗。
"""
import re

# 关键词 = 小写分词后的连续 token 串，命中即视为 SLG 竞品发行商。
# 故意只收「SLG 专精」厂商：Supercell / Scopely / Dream Games 等虽然
# 也在策略畅销榜，但非 SLG 口径（COC/大富翁/消除），不收。
SLG_PUBLISHER_KEYWORDS: tuple[str, ...] = (
    "century games",      # Whiteout Survival, Kingshot
    "building blocks",    # Puzzles & Survival / Puzzles & Chaos（Century 系工作室）
    "diandian",           # 点点互动
    "first fun",          # 部分地区 Whiteout 发行标
    "firstfun",
    "funplus",            # State of Survival, Sea of Conquest, Stormshot
    "kingsgroup",         # King of Avalon, Guns of Glory（FunPlus 系）
    "funfly",             # Last War: Survival
    "igg",                # Lords Mobile, Viking Rise
    "lilith",             # Rise of Kingdoms, Call of Dragons, Warpath
    "elex",               # Clash of Kings
    "camel games",        # Age of Origins / Age of Z（部分商店显示原名）
    "camelstudio",        # Age of Origins / War and Order（Android 主体）
    "ke mo",              # Age of Origins（Camel Games 香港主体）
    "tap4fun",            # Kingdom Guard, Kiss of War, Age of Apes
    "topwar",             # Top War
    "top war",
    "river game",         # Top War / Top Heroes（HK 主体）
    "rivergame",          # 同上（部分商店无空格）
    "long tech",          # Last Shelter, Rise of Castles（龙腾）
    "phantix",            # Mafia City, The Grand Mafia, Savage Survival（Yotta）
    "yotta games",        # 同上（部分商店主体）
    "starunion",          # The Ants: Underground Kingdom
    "star union",
    "37 mobile games",    # Puzzles & Survival（部分地区发行主体）
    "37games",
    "37 interactive",
    "top games",          # Evony（Top Games Inc）
    "onemt",              # Kingdom Guard 等
    "omnilojo",           # Last Z / Dark War: Survival
    "scorewarrior",       # Total Battle
    "yoozoo",             # Infinity Kingdom
    "life game",          # Last Fortress（LIFE GAME PTE / Life Game Global）
    "9z games",           # X-Clash: Survival
    "tilting point",      # 多款 SLG 联运
    "machine zone",       # Game of War, Mobile Strike
    "stillfront",         # Conflict of Nations, Supremacy
    "innogames",          # Forge of Empires, Tribal Wars
    "joycity",            # Gunship Battle: Total Warfare
)

# 多品类大厂旗下个别真·SLG，按发行商收会带进一堆非 SLG，只能按 app_id 钉。
# 跨商店：同一游戏 iOS 数字 id 与 Android 包名各算一个 key（两端 id 不同）。
SLG_APP_IDS: frozenset[str] = frozenset({
    "6476261995", "com.proximabeta.aoemobile",   # Age of Empires Mobile（Level Infinite）
    "1035712810", "com.wb.goog.got.conquest",     # Game of Thrones: Conquest（Warner Bros）
    "1427744264", "com.scopely.startrek",         # Star Trek Fleet Command（Scopely）
    "com.plarium.vikings",                         # Vikings: War of Clans（Plarium，旗下 RAID 非 SLG）
    "com.farlightgames.samo.gp",                   # Call of Dragons（Farlight，旗下 Farlight 84 非 SLG）
    "6756989323",                                  # Last Asylum: Plague
    "com.slg.policewar",                           # Police Chief
})

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


def is_slg(app_id: str | None, publisher: str | None) -> bool:
    """app_id 命中关注名单，或发行商命中白名单 → 视为 SLG 竞品。"""
    if app_id and app_id in SLG_APP_IDS:
        return True
    return is_slg_publisher(publisher)
