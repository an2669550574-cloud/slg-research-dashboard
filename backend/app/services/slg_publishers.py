"""SLG 竞品判定：厂商主体（publisher_entities）驱动的发行商马甲 + app_id 关注名单。

应用商店最细只到「策略」大类（iOS 7017 / Android game_strategy），没有「SLG」
子类，所以策略畅销榜天然混进棋牌 / 塔防 / 卡牌策略等非 SLG 产品。这里做
**后置过滤**——零额外配额（榜单已全量拉回，只给每行打 is_slg 标记，前端可切
「仅 SLG / 全部策略」）。

两路判定（与历史一致）：
1. 发行商马甲（PublisherAlias.keyword）—— SLG 专精发行商，覆盖其全部产品。
2. 关注 app_id（PublisherAppId.app_id）—— 多品类大厂（Warner Bros / Scopely /
   Level Infinite / Plarium / Farlight）旗下个别真·SLG，按发行商收会连带一堆
   非 SLG，只能按 app_id 精确钉。

**数据源已从硬编码常量迁到 DB**（厂商主体三表），可在看板上维护、沉淀主体关联，
不用改代码发版。运行时 is_slg() 仍是同步函数，查的是**内存索引**（启动时
load_index_from_db 加载、CRUD 变更后 refresh）——movement / sensor_tower / games
等在循环里逐行调用的热路径无需 DB 往返，签名也不变。

下方 SEED_PUBLISHERS 是内置起步种子：① scheduler.seed_publishers_if_empty 在
publisher_entities 空表时灌入；② 作为内存索引的冷启动兜底（DB 未加载 / 测试未
seed 时 is_slg 仍按种子判定，行为与迁移前完全一致）。种子的 keyword/app_id 全集
与迁移前的白名单逐一对应，保证榜单过滤与异动检测零回归。

主体归并依据：沿用迁移前白名单注释里已标注的「系」（FunPlus 系 = funplus/
kingsgroup；Century 系 = century games/building blocks/first fun 等），不臆造新关联；
注释未标系的发行商各自独立成主体。hq_region 只对业内公认的中国厂商标「国内」、
欧美韩厂标「海外」，拿不准的留空。
"""
import re
from typing import Optional


# ── 内置起步种子：主体 → 海外发行马甲(keyword,label) + 关注 app_id(app_id,note) ──
SEED_PUBLISHERS: tuple[dict, ...] = (
    # 点点互动（世纪华通全资子公司）与其海外品牌 Century Games 是同一运营体，合并为单主体；
    # century games / building blocks 是其海外发行马甲，diandian 为国内名兜底。
    # 注：first fun / firstfun 不归点点——First Fun 是元趣娱乐（Last War 研发商），见下「元趣娱乐」条。
    {"name": "点点互动", "name_en": "Diandian Interactive / Century Games", "hq_region": "国内", "is_slg": True,
     "brief": "世纪华通全资子公司；海外发行品牌 Century Games（新加坡发行主体 Century Games Pte. Ltd.）。Whiteout Survival / Kingshot；Puzzles & Survival 为系工作室 Building Blocks 出品。",
     "aliases": [("century games", "Century Games"), ("building blocks", "Building Blocks"),
                 ("diandian", "Diandian")], "app_ids": []},
    {"name": "FunPlus", "name_en": "FunPlus", "hq_region": "国内", "is_slg": True,
     "brief": "State of Survival / Sea of Conquest / Stormshot；KingsGroup（King of Avalon / Guns of Glory）为 FunPlus 系",
     "aliases": [("funplus", "FunPlus"), ("kingsgroup", "KingsGroup")], "app_ids": []},
    {"name": "Funfly", "name_en": "Funfly", "hq_region": "海外", "is_slg": True,
     "brief": "Last War: Survival 发行马甲", "aliases": [("funfly", "Funfly")], "app_ids": []},
    {"name": "IGG", "name_en": "IGG", "hq_region": "海外", "is_slg": True,
     "brief": "Lords Mobile / Viking Rise", "aliases": [("igg", "IGG")], "app_ids": []},
    {"name": "莉莉丝", "name_en": "Lilith Games", "hq_region": "国内", "is_slg": True,
     "brief": "Rise of Kingdoms / Call of Dragons / Warpath", "aliases": [("lilith", "Lilith Games")], "app_ids": []},
    {"name": "ELEX", "name_en": "ELEX", "hq_region": "国内", "is_slg": True,
     "brief": "Clash of Kings", "aliases": [("elex", "ELEX")], "app_ids": []},
    {"name": "骆驼游戏 Camel Games", "name_en": "Camel Games", "hq_region": "国内", "is_slg": True,
     "brief": "Age of Origins / Age of Z / War and Order（多商店主体：Camel Games / CamelStudio / Ke Mo 香港主体）",
     "aliases": [("camel games", "Camel Games"), ("camelstudio", "CamelStudio"), ("ke mo", "Ke Mo")], "app_ids": []},
    {"name": "Tap4fun", "name_en": "Tap4fun", "hq_region": "国内", "is_slg": True,
     "brief": "Kingdom Guard / Kiss of War / Age of Apes", "aliases": [("tap4fun", "Tap4fun")], "app_ids": []},
    {"name": "River Game", "name_en": "River Game", "hq_region": "海外", "is_slg": True,
     "brief": "Top War / Top Heroes（HK 主体；多商店标 TopWar / River Game）",
     "aliases": [("topwar", "TopWar"), ("top war", "Top War"),
                 ("river game", "River Game"), ("rivergame", "River Game")], "app_ids": []},
    {"name": "龙腾简合 Long Tech", "name_en": "Long Tech", "hq_region": "国内", "is_slg": True,
     "brief": "Last Shelter: Survival / Rise of Castles", "aliases": [("long tech", "Long Tech")], "app_ids": []},
    {"name": "Yotta Games", "name_en": "Yotta Games", "hq_region": "海外", "is_slg": True,
     "brief": "Mafia City / The Grand Mafia / Savage Survival（发行标 Phantix）",
     "aliases": [("phantix", "Phantix"), ("yotta games", "Yotta Games")], "app_ids": []},
    {"name": "StarUnion 星合", "name_en": "StarUnion", "hq_region": "国内", "is_slg": True,
     "brief": "The Ants: Underground Kingdom", "aliases": [("starunion", "StarUnion"), ("star union", "Star Union")], "app_ids": []},
    {"name": "三七互娱", "name_en": "37 Interactive", "hq_region": "国内", "is_slg": True,
     "brief": "Puzzles & Survival 部分地区发行主体",
     "aliases": [("37 mobile games", "37 Mobile Games"), ("37games", "37Games"),
                 ("37 interactive", "37 Interactive")], "app_ids": []},
    {"name": "Top Games", "name_en": "Top Games Inc", "hq_region": "海外", "is_slg": True,
     "brief": "Evony: The King's Return", "aliases": [("top games", "Top Games")], "app_ids": []},
    {"name": "OneMT", "name_en": "OneMT", "hq_region": "国内", "is_slg": True,
     "brief": "Kingdom Guard 等（主攻中东市场）", "aliases": [("onemt", "OneMT")], "app_ids": []},
    {"name": "Omnilojo", "name_en": "Omnilojo", "hq_region": None, "is_slg": True,
     "brief": "Last Z / Dark War: Survival", "aliases": [("omnilojo", "Omnilojo")], "app_ids": []},
    {"name": "Scorewarrior", "name_en": "Scorewarrior", "hq_region": "海外", "is_slg": True,
     "brief": "Total Battle", "aliases": [("scorewarrior", "Scorewarrior")], "app_ids": []},
    {"name": "游族 YOOZOO", "name_en": "YOOZOO", "hq_region": "国内", "is_slg": True,
     "brief": "Infinity Kingdom", "aliases": [("yoozoo", "YOOZOO")], "app_ids": []},
    {"name": "Life Game", "name_en": "Life Game", "hq_region": None, "is_slg": True,
     "brief": "Last Fortress: Underground（Life Game PTE / Life Game Global）", "aliases": [("life game", "Life Game")], "app_ids": []},
    {"name": "9z Games", "name_en": "9z Games", "hq_region": None, "is_slg": True,
     "brief": "X-Clash: Survival", "aliases": [("9z games", "9z Games")], "app_ids": []},
    {"name": "Tilting Point", "name_en": "Tilting Point", "hq_region": "海外", "is_slg": True,
     "brief": "多款 SLG 联运发行", "aliases": [("tilting point", "Tilting Point")], "app_ids": []},
    {"name": "Machine Zone", "name_en": "Machine Zone", "hq_region": "海外", "is_slg": True,
     "brief": "Game of War / Mobile Strike", "aliases": [("machine zone", "Machine Zone")], "app_ids": []},
    {"name": "Stillfront", "name_en": "Stillfront Group", "hq_region": "海外", "is_slg": True,
     "brief": "Conflict of Nations / Supremacy", "aliases": [("stillfront", "Stillfront")], "app_ids": []},
    {"name": "InnoGames", "name_en": "InnoGames", "hq_region": "海外", "is_slg": True,
     "brief": "Forge of Empires / Tribal Wars", "aliases": [("innogames", "InnoGames")], "app_ids": []},
    {"name": "JoyCity", "name_en": "JoyCity", "hq_region": "海外", "is_slg": True,
     "brief": "Gunship Battle: Total Warfare（韩）", "aliases": [("joycity", "JoyCity")], "app_ids": []},
    # ── 多品类大厂：旗下个别真·SLG 按 app_id 精确钉（无 alias，按发行商收会带进非 SLG）──
    {"name": "Level Infinite", "name_en": "Level Infinite", "hq_region": "海外", "is_slg": True,
     "brief": "腾讯海外发行品牌；Age of Empires Mobile（Proxima Beta）", "aliases": [],
     "app_ids": [("6476261995", "Age of Empires Mobile (iOS)"),
                 ("com.proximabeta.aoemobile", "Age of Empires Mobile (Android)")]},
    {"name": "Warner Bros", "name_en": "Warner Bros. Games", "hq_region": "海外", "is_slg": True,
     "brief": "Game of Thrones: Conquest", "aliases": [],
     "app_ids": [("1035712810", "Game of Thrones: Conquest (iOS)"),
                 ("com.wb.goog.got.conquest", "Game of Thrones: Conquest (Android)")]},
    {"name": "Scopely", "name_en": "Scopely", "hq_region": "海外", "is_slg": True,
     "brief": "Star Trek Fleet Command（旗下 Monopoly Go 等非 SLG）", "aliases": [],
     "app_ids": [("1427744264", "Star Trek Fleet Command (iOS)"),
                 ("com.scopely.startrek", "Star Trek Fleet Command (Android)")]},
    {"name": "Plarium", "name_en": "Plarium", "hq_region": "海外", "is_slg": True,
     "brief": "Vikings: War of Clans（旗下 RAID 非 SLG）", "aliases": [],
     "app_ids": [("com.plarium.vikings", "Vikings: War of Clans (Android)")]},
    {"name": "Farlight Games", "name_en": "Farlight Games", "hq_region": "海外", "is_slg": True,
     "brief": "Call of Dragons（旗下 Farlight 84 非 SLG）", "aliases": [],
     "app_ids": [("com.farlightgames.samo.gp", "Call of Dragons (Android)")]},
    {"name": "关注单品（主体待确认）", "name_en": None, "hq_region": None, "is_slg": True,
     "brief": "按 app_id 钉的关注 SLG 单品，发行主体待补充", "aliases": [],
     "app_ids": [("6756989323", "Last Asylum: Plague"), ("com.slg.policewar", "Police Chief")]},
    # ── 国内主体调研起点（领导点名）：先建壳，海外发行马甲关联待人工确认后在看板补 ──
    {"name": "江娱互动", "name_en": "Jiangyu Interactive", "hq_region": "国内", "is_slg": True,
     "brief": "国内 SLG 出海厂商，创始人吴凌江（前智明星通联创）；代表作 口袋奇兵 / Top War（海外主体 River Game）。元趣娱乐持股 35.63%。", "aliases": [], "app_ids": []},
    # 元趣娱乐 = First Fun（Last War 研发商），原 first fun/firstfun alias 从点点迁来归此主体；
    # 与江娱互动同系（谢贤林创办元趣、持江娱 35.63%）。海外发行走旗下 Funfly。
    {"name": "元趣娱乐", "name_en": "First Fun", "hq_region": "国内", "is_slg": True,
     "brief": "北京元趣娱乐（First Fun），谢贤林（前智明星通联创/总裁）创办；Last War: Survival 研发商，海外由旗下 Funfly 发行；持江娱互动 35.63% 股份。",
     "aliases": [("first fun", "First Fun"), ("firstfun", "First Fun")], "app_ids": []},
)


def _tokens(s: Optional[str]) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split()


# ── 内存索引：运行时 is_slg 查这里。DB 为源，启动 load、CRUD 后 refresh。 ──
_alias_tokens: list[tuple[str, ...]] = []  # 每个马甲 keyword 的连续 token 串
_app_ids: set[str] = set()


def _set_index(keywords, app_id_list) -> None:
    """用给定 keyword/app_id 集合重建内存索引（覆盖式）。"""
    global _alias_tokens, _app_ids
    _alias_tokens = [tuple(t) for t in (_tokens(k) for k in keywords) if t]
    _app_ids = set(app_id_list)


def _seed_keywords() -> list[str]:
    return [kw for p in SEED_PUBLISHERS for kw, _ in p["aliases"]]


def _seed_app_ids() -> list[str]:
    return [aid for p in SEED_PUBLISHERS for aid, _ in p["app_ids"]]


# 模块导入即用种子填充索引——作为 DB 未加载时的兜底，保证 is_slg 行为与迁移前一致。
_set_index(_seed_keywords(), _seed_app_ids())


async def load_index_from_db() -> int:
    """从 publisher_aliases / publisher_app_ids 重建内存索引。返回加载的马甲条数。

    DB 全空（极早期 / 未 seed）时**保留种子兜底不清空**——否则 is_slg 会全 False，
    movement 把昨日 TopN SLG 全员错报为跌出。startup 在 seed 之后调，正常不会空。
    """
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.publisher import PublisherAlias, PublisherAppId

    async with AsyncSessionLocal() as db:
        keywords = (await db.execute(select(PublisherAlias.keyword))).scalars().all()
        app_id_list = (await db.execute(select(PublisherAppId.app_id))).scalars().all()
    if not keywords and not app_id_list:
        return 0  # 不覆盖种子兜底
    _set_index(keywords, app_id_list)
    return len(keywords)


def is_slg_publisher(publisher: Optional[str]) -> bool:
    """publisher 的 token 序列里出现任一马甲 keyword（作为连续子序列）即 True。

    publisher 为空（如 Android 富化失败没抓到发行商）→ False：宁可在「仅 SLG」
    视图漏掉、让用户切「全部策略」补看，也不污染默认视图。
    """
    toks = _tokens(publisher)
    if not toks:
        return False
    for kw in _alias_tokens:
        n = len(kw)
        for i in range(len(toks) - n + 1):
            if tuple(toks[i:i + n]) == kw:
                return True
    return False


def is_slg(app_id: Optional[str], publisher: Optional[str]) -> bool:
    """app_id 命中关注名单，或发行商命中马甲白名单 → 视为 SLG 竞品。"""
    if app_id and app_id in _app_ids:
        return True
    return is_slg_publisher(publisher)
