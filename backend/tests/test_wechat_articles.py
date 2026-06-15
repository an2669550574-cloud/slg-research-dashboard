"""微信公众号文章接入：文章→app_id 匹配、行渲染、标题清洗。

重点回归：_match_articles_to_apps 必须能容忍 per_combo 里 market/publisher 为 None
的 combo（曾用 c.get("market", {}) 在 key 存在为 None 时 AttributeError，导致整段
文章匹配被 try/except 吞掉、功能静默失效）。
"""
from app.services.wechat_articles import WechatArticle, _strip_html
from app.services.release_alerts import (
    _match_articles_to_apps, _articles_suffix, build_newcomer_lines,
)


def _art(title, link="https://mp.weixin.qq.com/s/x", digest=""):
    return WechatArticle(title=title, link=link, digest=digest, author="游戏葡萄")


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
