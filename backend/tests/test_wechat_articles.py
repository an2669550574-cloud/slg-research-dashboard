"""微信公众号文章接入：文章→app_id 匹配、行渲染、标题清洗。

重点回归：_match_articles_to_apps 必须能容忍 per_combo 里 market/publisher 为 None
的 combo（曾用 c.get("market", {}) 在 key 存在为 None 时 AttributeError，导致整段
文章匹配被 try/except 吞掉、功能静默失效）。
"""
import pytest

from app.services import wechat_articles as wa
from app.services.wechat_articles import WechatArticle, WechatLoginStatus, _strip_html
from app.services.release_alerts import (
    _match_articles_to_apps, _articles_suffix, build_newcomer_lines,
    build_wechat_expiry_alert,
)

_NOW = 1_781_000_000.0  # 固定"现在"（秒），避免依赖真实时钟
_DAY_MS = 86_400_000


def test_wechat_alert_none_when_service_unreachable():
    # status=None（连不上）不能误报「过期」
    assert build_wechat_expiry_alert(None, _NOW, 3) is None


def test_wechat_alert_none_when_healthy_and_far():
    st = WechatLoginStatus(logged_in=True, is_expired=False,
                           expire_time_ms=int((_NOW + 30 * 86400) * 1000))
    assert build_wechat_expiry_alert(st, _NOW, 3) is None


def test_wechat_alert_expired():
    st = WechatLoginStatus(logged_in=True, is_expired=True)
    out = build_wechat_expiry_alert(st, _NOW, 3)
    assert out is not None and "失效" in out[0] and "login.html" in out[1]


def test_wechat_alert_not_logged_in():
    st = WechatLoginStatus(logged_in=False, is_expired=False)
    assert build_wechat_expiry_alert(st, _NOW, 3) is not None


def test_wechat_alert_expiring_soon_within_warn_days():
    # 还有 2 天过期（warn=3）→ 预警；ms 时间戳要正确换算
    st = WechatLoginStatus(logged_in=True, is_expired=False,
                           expire_time_ms=int(_NOW * 1000) + 2 * _DAY_MS)
    out = build_wechat_expiry_alert(st, _NOW, 3)
    assert out is not None and "过期" in out[0]


def test_wechat_alert_has_scan_button(monkeypatch):
    """配了 DASHBOARD_BASE_URL → 提醒带「扫码续期」按钮直达看板登录页（开页即实时二维码）。"""
    # patch release_alerts 实际用的 settings 对象（不走 app.config.settings 字符串路径——
    # 跨测试若有人整体替换/重载 settings 会让字符串路径指向另一个实例，导致漏改）。
    import app.services.release_alerts as ra
    monkeypatch.setattr(ra.settings, "DASHBOARD_BASE_URL", "https://board.example.com")
    st = WechatLoginStatus(logged_in=True, is_expired=True)
    # 用 ra.build_*（当前模块）而非顶部 import 的旧引用——client fixture 会 del sys.modules
    # 重导入 app.*，旧引用读旧 settings、与所 patch 的 ra.settings 不是同一对象。
    _, _, btns = ra.build_wechat_expiry_alert(st, _NOW, 1)
    assert len(btns) == 1
    assert "续期" in btns[0][0] and btns[0][1] == "https://board.example.com/wechat-login"


def test_wechat_alert_no_button_without_base_url(monkeypatch):
    """未配 DASHBOARD_BASE_URL → 无按钮（send_action_card 会自动降级 markdown，仅留 ssh 兜底）。"""
    import app.services.release_alerts as ra
    monkeypatch.setattr(ra.settings, "DASHBOARD_BASE_URL", "")
    st = WechatLoginStatus(logged_in=True, is_expired=True)
    _, text, btns = ra.build_wechat_expiry_alert(st, _NOW, 1)
    assert btns == [] and "login.html" in text   # ssh 兜底仍在


def test_wechat_alert_tier_thresholds():
    """档位判定：≤12h=warn12 / ≤warn_days*24h=warn24 / 已失效=expired / 健康远期=None。"""
    from app.services.release_alerts import _wechat_alert_tier
    mk = lambda h: WechatLoginStatus(logged_in=True, is_expired=False,
                                     expire_time_ms=int(_NOW * 1000) + int(h * 3600 * 1000))
    assert _wechat_alert_tier(mk(6), _NOW, 1) == "warn12"
    assert _wechat_alert_tier(mk(20), _NOW, 1) == "warn24"
    assert _wechat_alert_tier(mk(40), _NOW, 1) is None        # 超 24h 不报
    assert _wechat_alert_tier(WechatLoginStatus(logged_in=True, is_expired=True), _NOW, 1) == "expired"
    assert _wechat_alert_tier(None, _NOW, 1) is None


@pytest.mark.asyncio
async def test_wechat_alert_dedup_by_tier(monkeypatch):
    """同一登录态（expire_ms 固定、now 推进）下：同档不重复推、升档（24h→12h）才再推。
    真实场景是同一 session 随时间从 warn24 滑到 warn12，故固定 expire、用 time.time 模拟时间走。"""
    import app.services.release_alerts as ra
    from app.config import settings
    monkeypatch.setattr(settings, "WECHAT_ENABLED", True)
    monkeypatch.setattr(settings, "WECHAT_EXPIRY_WARN_DAYS", 1)
    monkeypatch.setattr(ra.dingtalk, "is_enabled", lambda target="maintainer": True)
    sent = []
    async def fake_card(title, text, btns=None, target="maintainer", critical=False):
        sent.append(title); return True
    monkeypatch.setattr(ra.dingtalk, "send_action_card", fake_card)
    ra._wechat_alert_state.update({"expire_ms": None, "tier": None})  # 干净起点

    expire_ms = int(_NOW * 1000) + 24 * 3600 * 1000   # 固定：T0 + 24h 过期
    st = WechatLoginStatus(logged_in=True, is_expired=False, expire_time_ms=expire_ms)
    async def fake_status(): return st
    monkeypatch.setattr("app.services.wechat_articles.get_login_status", fake_status)

    clock = {"now": _NOW + 4 * 3600}      # T0+4h → 剩 20h → warn24
    monkeypatch.setattr(ra.time, "time", lambda: clock["now"])
    assert await ra.alert_wechat_login_if_needed() is True       # 首次 warn24 → 推
    clock["now"] = _NOW + 6 * 3600         # 剩 18h，仍 warn24
    assert await ra.alert_wechat_login_if_needed() is False      # 同档 → 不重复
    clock["now"] = _NOW + 14 * 3600        # 剩 10h → 升 warn12
    assert await ra.alert_wechat_login_if_needed() is True       # 升档 → 再推
    assert len(sent) == 2


def _art(title, link="https://mp.weixin.qq.com/s/x", digest=""):
    return WechatArticle(title=title, link=link, digest=digest, author="游戏葡萄")


def _wa_art(title, link):
    return WechatArticle(title=title, link=link, author="x", publish_time=1)


@pytest.mark.asyncio
async def test_multi_keyword_fallback_when_subscribed_miss(monkeypatch):
    """订阅号 0 命中 → 走 searchbiz 发现号兜底搜。"""
    calls = {"discover": 0}

    async def fake_search_articles(keyword, limit=3, days=180):
        return []  # 订阅号没搜到

    async def fake_enabled():
        return {"游戏葡萄": "FID_SUB=="}

    async def fake_search_biz(query, limit=8):
        return [{"fakeid": "FID_SUB==", "nickname": "已订阅(应被排除)", "alias": None},
                {"fakeid": "FID_NEW==", "nickname": "口袋奇兵官方", "alias": None}]

    async def fake_search_account(client, name, fakeid, keyword, cutoff):
        calls["discover"] += 1
        assert fakeid == "FID_NEW=="  # 已订阅的 FID_SUB 被排除
        return [_wa_art(f"{keyword} 新版本爆料", "https://mp.weixin.qq.com/s/new")]

    monkeypatch.setattr(wa, "search_articles", fake_search_articles)
    monkeypatch.setattr(wa, "_enabled_accounts", fake_enabled)
    monkeypatch.setattr(wa, "search_biz", fake_search_biz)
    monkeypatch.setattr(wa, "_search_account", fake_search_account)

    res = await wa.search_multi_keywords(["口袋奇兵"], limit=3)
    assert len(res) == 1 and "口袋奇兵" in res[0].title
    assert calls["discover"] == 1  # 只搜了发现的那个非订阅号


@pytest.mark.asyncio
async def test_multi_keyword_no_fallback_when_subscribed_hit(monkeypatch):
    """订阅号有命中 → 不触发 searchbiz 兜底。"""
    async def fake_search_articles(keyword, limit=3, days=180):
        return [_wa_art("订阅号命中", "https://mp.weixin.qq.com/s/sub")]

    async def boom_search_biz(query, limit=8):
        raise AssertionError("命中时不应调用 searchbiz")

    monkeypatch.setattr(wa, "search_articles", fake_search_articles)
    monkeypatch.setattr(wa, "search_biz", boom_search_biz)

    res = await wa.search_multi_keywords(["原神"], limit=3)
    assert len(res) == 1 and res[0].title == "订阅号命中"


def test_strip_html_removes_highlight_tags():
    """搜索结果 title 会包 <em class="highlight">…</em>，必须清掉 + 反转义留纯文本。"""
    assert _strip_html('离职腾讯后，他在新<em class="highlight">游戏</em>塞了AI') == "离职腾讯后，他在新游戏塞了AI"
    assert _strip_html(None) == ""
    assert _strip_html("  纯文本  ") == "纯文本"


def test_match_articles_survives_none_market_combo():
    """有 market=None 的 combo 也不能抛——这是核心回归。"""
    per_combo = [
        {"market": None, "publisher": None},  # 当天无数据的 combo
        {"market": {"newcomers": [{"name": "口袋奇兵", "app_id": "111"}]},
         "publisher": None},
        {"market": None,
         "publisher": {"newcomers": [{"name": "万国觉醒", "app_id": "222"}]}},
    ]
    articles = [_art("口袋奇兵海外买量复盘"), _art("万国觉醒新赛季解读")]
    out = _match_articles_to_apps(per_combo, articles)
    assert out["111"][0].title == "口袋奇兵海外买量复盘"
    assert out["222"][0].title == "万国觉醒新赛季解读"


def test_match_articles_matches_digest_and_dedups_app():
    """标题不含名、摘要含名也算命中；同名跨 combo 的多个 app_id 都挂上。"""
    per_combo = [
        {"market": {"newcomers": [{"name": "Last War", "app_id": "a1"}]}, "publisher": None},
        {"market": {"newcomers": [{"name": "Last War", "app_id": "a2"}]}, "publisher": None},
    ]
    arts = [_art("一篇分析", digest="深扒 Last War 的留存设计")]
    out = _match_articles_to_apps(per_combo, arts)
    assert set(out.keys()) == {"a1", "a2"}


def test_match_articles_no_false_attach():
    per_combo = [{"market": {"newcomers": [{"name": "口袋奇兵", "app_id": "111"}]}, "publisher": None}]
    out = _match_articles_to_apps(per_combo, [_art("完全无关的一篇文章")])
    assert out == {}


def test_match_articles_skips_single_char_cjk_name():
    """A3：1 字 CJK 名（"城"）裸 substring 必泛滥误挂——跳过不匹配（宁漏不误）。"""
    per_combo = [{"market": {"newcomers": [{"name": "城", "app_id": "c1"}]}, "publisher": None}]
    out = _match_articles_to_apps(per_combo, [_art("城市更新公告与城建规划")])
    assert out == {}


def test_match_articles_keeps_two_char_cjk_name():
    """A3：2 字真名（"原神"）达最小名长，仍正常命中——不被长度门槛误杀。"""
    per_combo = [{"market": {"newcomers": [{"name": "原神", "app_id": "g1"}]}, "publisher": None}]
    out = _match_articles_to_apps(per_combo, [_art("原神 4.0 版本攻略")])
    assert set(out.keys()) == {"g1"}


def test_match_articles_latin_word_boundary():
    """A3：拉丁名走词边界——"Last War" 不再误挂 "Last Warning"，独立出现才命中。"""
    per_combo = [{"market": {"newcomers": [{"name": "Last War", "app_id": "a1"}]}, "publisher": None}]
    # 嵌在更长词里：不命中
    assert _match_articles_to_apps(per_combo, [_art("Last Warning 测评")]) == {}
    # 独立词：命中
    assert set(_match_articles_to_apps(per_combo, [_art("Last War 海外买量")]).keys()) == {"a1"}


def test_match_articles_latin_case_insensitive():
    """A3：拉丁名大小写无关——原本大小写敏感 substring 会漏 "last war"。"""
    per_combo = [{"market": {"newcomers": [{"name": "Last War", "app_id": "a1"}]}, "publisher": None}]
    out = _match_articles_to_apps(per_combo, [_art("一篇分析", digest="深扒 last war 的留存设计")])
    assert set(out.keys()) == {"a1"}


def test_match_articles_covers_free_chart_sources():
    """F1：下载榜新品（free_market/free_publisher）的名字也进搜索关键词，回挂必须同样覆盖
    这两层——否则【下载榜新品】行永远拿不到文章（搜了却挂不上）。"""
    per_combo = [{
        "market": None, "publisher": None,
        "free_market": {"newcomers": [{"name": "末日方舟", "app_id": "fm1"}]},
        "free_publisher": {"newcomers": [{"name": "Frontier City", "app_id": "fp1"}]},
    }]
    arts = [_art("末日方舟海外测评"), _art("Frontier City 上线买量复盘")]
    out = _match_articles_to_apps(per_combo, arts)
    assert "fm1" in out and "fp1" in out   # 下载榜两层都回挂得上


def test_newcomer_search_keywords_priority_and_determinism():
    """F2：关键词按 SLG>非回归>名次 优先级**确定性**截断；reentry 排末位、配额紧先截。"""
    from app.services.release_alerts import _newcomer_search_keywords
    per_combo = [{
        "market": {"newcomers": [
            {"name": "非SLG深位", "app_id": "m1", "is_slg": False, "rank": 40},
            {"name": "SLG头部", "app_id": "m2", "is_slg": True, "rank": 3},
            {"name": "SLG回归", "app_id": "m3", "is_slg": True, "is_reentry": True, "rank": 2},
        ]},
        "publisher": {"newcomers": [  # 已归属主体（无 is_slg 字段）→ 也算 SLG 优先级
            {"name": "已归属新品", "app_id": "p1", "entity_id": 8, "rank": 20},
        ]},
        "free_market": None, "free_publisher": None,
    }]
    # max_n=2：应取「SLG头部(rank3)」+「已归属新品(rank20)」——非SLG 与 SLG回归 被截掉
    top2 = _newcomer_search_keywords(per_combo, 2)
    assert top2 == ["SLG头部", "已归属新品"]
    # 全量顺序确定：SLG 非回归(按名次) > SLG 回归 > 非 SLG
    assert _newcomer_search_keywords(per_combo, 10) == [
        "SLG头部", "已归属新品", "SLG回归", "非SLG深位"]


def test_articles_suffix_sanitizes_title():
    """标题里的 ] | 换行会破坏钉钉 markdown 链接/分隔，必须清洗。"""
    a = _art("标题[含]特殊|字符\n换行", link="https://mp.weixin.qq.com/s/y")
    suffix = _articles_suffix([a])
    assert "📰" in suffix
    assert "]" not in suffix.split("](")[0]  # 链接文字部分无裸 ]
    assert "|" not in suffix.split("](")[0]
    assert "\n换行" not in suffix


def test_articles_suffix_empty():
    assert _articles_suffix(None) == ""
    assert _articles_suffix([]) == ""


def test_build_newcomer_lines_attaches_articles_both_sections():
    market = {"newcomers": [{"name": "口袋奇兵", "app_id": "111", "rank": 5,
                             "revenue": 1000, "is_slg": True, "publisher": "River Game"}]}
    publisher = {"newcomers": [{"name": "万国觉醒", "app_id": "222", "rank": 9,
                                "entity_name": "莉莉丝"}]}
    articles = {"111": [_art("口袋奇兵复盘")], "222": [_art("万国觉醒解读")]}
    lines = build_newcomer_lines(market, publisher, articles=articles)
    assert any("📰" in ln and "口袋奇兵复盘" in ln for ln in lines)
    assert any("📰" in ln and "万国觉醒解读" in ln for ln in lines)


def test_build_newcomer_lines_no_articles_no_suffix():
    market = {"newcomers": [{"name": "X", "app_id": "1", "rank": 1, "revenue": 0, "is_slg": True}]}
    lines = build_newcomer_lines(market, {}, articles={})
    assert all("📰" not in ln for ln in lines)
