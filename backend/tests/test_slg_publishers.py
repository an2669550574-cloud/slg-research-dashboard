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
