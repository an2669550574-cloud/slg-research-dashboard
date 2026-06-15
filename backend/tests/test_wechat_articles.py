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
