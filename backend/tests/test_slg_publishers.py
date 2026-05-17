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
