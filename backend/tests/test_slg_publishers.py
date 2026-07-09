"""SLG 发行商白名单匹配。conftest 每个 test 重载 app.* —— import 放函数内。"""
import pytest


@pytest.mark.parametrize("publisher", [
    "Century Games Pte. Ltd.",   # 公司后缀 + 标点
    "CENTURY GAMES PTE. LTD.",   # 全大写
    "FunPlus International AG",
    "IGG.COM",                   # .COM 后缀
    "Lilith Games",
    "ELEX Technology Co., Ltd.",
    "Top Games Inc",             # Evony
    "37 Mobile Games",
    "Machine Zone, Inc.",
    "Stillfront Group",
    # 以下为线上 /api/games/rankings 实测发行商串（部署后抓取，防回归）
    "FUNFLY PTE. LTD.",                       # Last War: Survival
    "Hong Kong Ke Mo software Co., Limited",  # Age of Origins（Camel Games HK）
    "BUILDING-BLOCKS NETWORK TECHNOLOGY CO.,LIMITED",  # Puzzles & Survival
    "River Game HK Limited",                  # Top War: Battle Game
    "TAP4FUN (HONGKONG) LIMITED",             # Kingdom Guard
    "IGG SINGAPORE PTE. LTD.",                # Lords Mobile
    "Omnilojo Pte Ltd",                       # Dark War: Survival
    "RiverGame",                              # Top Heroes（Android 无空格主体）
    "CamelStudio",                            # Age of Origins（Android 主体）
    "Long Tech Network Limited",              # Last Shelter / Rise of Castles
    "Phantix Games",                          # Mafia City / The Grand Mafia
    "StarUnion",                              # The Ants: Underground Kingdom
    "LIFE GAME PTE. LTD.",                    # Last Fortress: Underground
])
def test_known_slg_publishers_match(publisher):
    from app.services.slg_publishers import is_slg_publisher
    assert is_slg_publisher(publisher) is True


@pytest.mark.parametrize("publisher", [
    "Supercell",                 # COC：策略但非 SLG 口径
    "Scopely",                   # 大富翁
    "Dream Games Ltd.",          # Royal Match
    "King",                      # 消除
    "Playrix",
    "",
    None,
    "Some Random Studio",
])
def test_non_slg_publishers_rejected(publisher):
    from app.services.slg_publishers import is_slg_publisher
    assert is_slg_publisher(publisher) is False


def test_token_match_is_word_boundary_not_substring():
    """'igg' 关键词不能误命中含 'igg' 的无关单词（如 trigger games）。"""
    from app.services.slg_publishers import is_slg_publisher
    assert is_slg_publisher("Trigger Games") is False
    assert is_slg_publisher("IGG Singapore") is True


@pytest.mark.parametrize("publisher,expected", [
    # camelCase 连写：单 token alias（如种子里的 `lilith`）应能命中没空格的连写形式，
    # 不再依赖手动补 compact alias（如 `lilithgames`）
    ("LilithGames", True),     # 莉莉丝
    ("KingsGroup", True),       # FunPlus 系
    ("LongTech", True),         # 龙腾简合 alias=long tech
    # PascalCase 反向防误判：camelCase 拆出的 token 不能误中无关 alias
    ("TriggerGames", False),    # 'igg' 不能误命中 split 出的 token
    ("RiggerStudios", False),
    # 全大写一段（无 camelCase 边界可拆）应保持旧行为
    ("FUNFLY", True),
    ("STILLFRONT", True),
    # camelCase 连写也不应破坏既有 compact alias 兼容（CamelStudio 走 compact alias 路径）
    ("CamelStudio", True),
])
def test_camelcase_publisher_matches_via_split(publisher, expected):
    from app.services.slg_publishers import is_slg_publisher
    assert is_slg_publisher(publisher) is expected


@pytest.mark.parametrize("publisher,expected", [
    # 连写 + 法人后缀：alias "top games"（["top","games"]）配真实发行商连写形式。
    # 子序列要 token 边界对齐配不上，靠 corp_squash 去后缀拼接后整段等值兜底。
    ("Topgames.Inc", True),       # squash "topgames" == alias squash "topgames"
    ("TOPGAMES INC", True),
    ("Topgames Pte. Ltd.", True),
    # squash 只做**等值**不做子串：不能让短 alias 误命中更长连写名（word-boundary 回归）
    ("Trigger Games Inc", False), # squash "triggergames" != 任何 alias squash
    ("Topgamestudio", False),     # 多了真实词 "studio"，squash != "topgames"
])
def test_squash_fallback_matches_glued_corporate_names(publisher, expected):
    from app.services.slg_publishers import is_slg_publisher
    assert is_slg_publisher(publisher) is expected


@pytest.mark.parametrize("app_id,publisher", [
    ("6476261995", "Level Infinite"),          # Age of Empires Mobile（iOS）
    ("com.proximabeta.aoemobile", "Level Infinite"),  # 同（Android）
    ("1035712810", "Warner Bros."),            # GoT: Conquest
    ("com.wb.goog.got.conquest", "Warner Bros. International Enterprises"),
    ("1427744264", "Scopely, Inc."),           # Star Trek Fleet Command
    ("com.plarium.vikings", "Plarium  LLC"),   # Vikings: War of Clans
    ("com.farlightgames.samo.gp", "FARLIGHT"), # Call of Dragons
])
def test_watchlist_app_id_overrides_non_slg_publisher(app_id, publisher):
    """多品类发行商旗下被钉的真·SLG：app_id 命中即 True，哪怕发行商不在白名单。"""
    from app.services.slg_publishers import is_slg
    assert is_slg(app_id, publisher) is True


def test_is_slg_falls_back_to_publisher_when_appid_not_pinned():
    from app.services.slg_publishers import is_slg
    assert is_slg("999999999", "Century Games Pte. Ltd.") is True   # 发行商路命中
    assert is_slg("999999999", "Supercell") is False                # 两路都不中
    assert is_slg(None, "FunPlus International AG") is True          # app_id 缺失不影响发行商路


def test_non_ascii_alias_substring_match():
    """非拉丁 alias（韩/日文马甲）走 substring 路径：_tokens 按 [a-z0-9] 分词会把
    CJK keyword 滤成空 token，原实现直接丢弃——人工建韩文马甲也永远命不中。
    次市场商店返回本地化 publisher 串是 is_slg 跨 combo 分裂的根因之一。"""
    from app.services import slg_publishers as sp
    sp._set_index(sp._seed_keywords() + ["조이시티", "ジョイシティ"], sp._seed_app_ids())
    assert sp.is_slg_publisher("주식회사 조이시티") is True      # 韩文法人前缀 + 马甲
    assert sp.is_slg_publisher("株式会社ジョイシティ") is True   # 日文连写
    assert sp.is_slg_publisher("JOYCITY Corporation") is True   # token 路径不受影响
    assert sp.is_slg_publisher("어떤 무관한 회사") is False
    assert sp.is_slg_publisher("") is False and sp.is_slg_publisher(None) is False


def test_non_ascii_alias_single_char_dropped():
    """单字符非拉丁 alias 防过匹配：len<2 不进 substring 索引。"""
    from app.services import slg_publishers as sp
    sp._set_index(sp._seed_keywords() + ["조"], sp._seed_app_ids())
    assert sp.is_slg_publisher("조이시티") is False
