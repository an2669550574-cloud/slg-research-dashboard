"""监测 → 钉钉推送：把"有事发生"主动送到群里，不用人盯页面。

2026-06-12 体验改版（人话化 + ActionCard + 日级聚合）：
- **每日情报汇总**（竞品异动 + 两层新品，全 combo 合并一条）：不再随每个 combo
  同步各发各的——03:00 UTC（北京 11:00，核心同步 02:30~02:38 之后）日级 job
  对全部 combo **重跑检测**（纯本地库读、零配额、无状态），拼成一张卡发一次。
  只纳入当天有新快照的 combo（as_of==today / today_missing 闸门），次市场的
  旧快照不会被反复重报。
- **应用商店雷达**（iOS+GP 清单 diff）保持每轮检出即推（6h 级，时效优先），
  但换 ActionCard：底部按钮直达商店页。
- 文案口径与 Sentry 日志分离：日志保留 [NEW]/[UP] 机器码（movement._format_parts），
  群消息一律人读文案（emoji 分类 + 关键数字加粗）。

所有发送走 services/dingtalk（未配 webhook = 静默 no-op；失败不抛、不拖垮任务）。
digest 构建是纯函数，单测直接断言 markdown 文本。
"""
import logging
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.publisher import PublisherEntity, PublisherItunesApp, PublisherItunesArtist
from app.models.game import CHART_FREE
from app.services import dingtalk

logger = logging.getLogger(__name__)

_COMBO_FLAG = {"us": "🇺🇸", "jp": "🇯🇵", "kr": "🇰🇷", "cn": "🇨🇳", "tw": "🇹🇼", "de": "🇩🇪", "gb": "🇬🇧"}
# 市场 / 平台中文标签（领导面向，去英文）。漏配的国家码回退大写原文。
_COUNTRY_CN = {"us": "美国", "jp": "日本", "kr": "韩国", "cn": "中国", "tw": "台湾",
               "de": "德国", "gb": "英国", "au": "澳洲", "ca": "加拿大", "fr": "法国"}
_PLATFORM_CN = {"ios": "iOS", "android": "安卓"}
# 应用商店品类英文 → 中文（SLG 监控里绝大多数是策略子类，其余常见类一并覆盖）。
_GENRE_CN = {
    "strategy": "策略", "simulation": "模拟经营", "casual": "休闲", "puzzle": "解谜",
    "role playing": "角色扮演", "rpg": "角色扮演", "action": "动作", "adventure": "冒险",
    "card": "卡牌", "board": "桌游", "arcade": "街机", "racing": "竞速", "sports": "体育",
}


def _fmt_num(v) -> str:
    """大数压成 K/M（领导看规模而非精确值）：1234→1K，2_020_000→2.0M。"""
    if not v:
        return "—"
    v = float(v)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return f"{v:.0f}"


def _fmt_money(v) -> str:
    return f"${_fmt_num(v)}" if v else "—"


def _genre_cn(g: Optional[str]) -> Optional[str]:
    if not g:
        return None
    return _GENRE_CN.get(g.strip().lower(), g)


def _market_label(country: str, platform: str) -> str:
    """市场+平台标识（不带榜种），如「🇺🇸 美国 · 安卓」。下载榜/跨段复用，避免
    `_combo_label` 的「畅销榜」后缀与下载榜语境打架。"""
    flag = _COMBO_FLAG.get(country.lower(), "")
    cc = _COUNTRY_CN.get(country.lower(), country.upper())
    pf = _PLATFORM_CN.get(platform.lower(), platform)
    return f"{flag} {cc} · {pf}".strip()


def _combo_label(country: str, platform: str) -> str:
    return f"{_market_label(country, platform)} 畅销榜"


def _meta_line(*, genre=None, revenue=None, downloads=None, entity=None) -> str:
    """条目下方的中文富化子行：品类 · 日收入 · 下载 · 厂商。全空则不占行。
    用 markdown 引用块（`> `）渲染成灰色竖条子行——与主标题行形成层次、折行自带缩进。"""
    parts = []
    if (g := _genre_cn(genre)):
        parts.append(g)
    if revenue:
        parts.append(f"日收入 {_fmt_money(revenue)}")
    if downloads:
        parts.append(f"下载 {_fmt_num(downloads)}")
    if entity:
        parts.append(f"厂商 {entity}")
    if not parts:
        return ""
    return "\n> " + " · ".join(parts)


def _store_url(app_id: str, country: str, platform: str) -> Optional[str]:
    """榜单行 app_id → 商店页链接。iOS 数字 id 拼 App Store；安卓包名（含 `.`）拼
    Google Play。其余形态（空/异常）拼不出返回 None。"""
    aid = str(app_id or "").strip()
    if platform == "ios" and aid.isdigit():
        return f"https://apps.apple.com/{country.lower()}/app/id{aid}"
    if platform == "android" and "." in aid and " " not in aid:
        return f"https://play.google.com/store/apps/details?id={aid}"
    return None


def _dashboard_focus_url(app_id: str, view: str) -> Optional[str]:
    """新品行 → 看板深链（进新品页 ?focus=<app_id> 定位高亮该卡）。view 决定落地视图
    （market=全市场新面孔 / publisher=厂商新品）。未配 DASHBOARD_BASE_URL 返回 None
    （digest 不拼深链，向后兼容）。"""
    base = (settings.DASHBOARD_BASE_URL or "").rstrip("/")
    aid = str(app_id or "").strip()
    if not base or not aid:
        return None
    return f"{base}/newcomers?focus={quote(aid, safe='')}&view={view}"


# ── 每日情报汇总（竞品异动 + 两层新品，全 combo 一条） ─────────────────────

def build_movement_lines(s: dict, entities: Optional[dict] = None,
                         cap: Optional[int] = None) -> list[str]:
    """movement 摘要 → 人读行。与 Sentry 的 [NEW]/[UP] 机器码刻意分离。
    entities: {app_id: 中文厂商主体} —— 给每条补「日收入 · 下载 · 厂商归属」子行。
    cap: 单 combo 展示行上限（按 空降/窜升/暴跌/收入异动 顺序保留前 cap 条，
    砍掉重要性较低的尾部），None=不限。"""
    entities = entities or {}

    def _meta(e):
        return _meta_line(revenue=e.get("revenue"), downloads=e.get("downloads"),
                          entity=entities.get(e.get("app_id")) or e.get("publisher"))

    lines = []
    for e in s["new_entrants"]:
        frm = "榜外" if e["prev_rank"] is None else f"#{e['prev_rank']}"
        lines.append(f"🆕 **{e['name']}** 空降 **#{e['cur_rank']}**（{frm} →）" + _meta(e))
    for e in s["surges"]:
        lines.append(f"📈 **{e['name']}** #{e['prev_rank']} → **#{e['cur_rank']}**（↑{e['prev_rank'] - e['cur_rank']}）" + _meta(e))
    for e in s["drops"]:
        to = "榜外" if e["cur_rank"] is None else f"#{e['cur_rank']}"
        lines.append(f"📉 **{e['name']}** 跌出 Top 榜（#{e['prev_rank']} → {to}）" + _meta(e))
    for e in s["revenue_spikes"]:
        # 收入异动主行已带前后金额，厂商归属**内联行尾**（不另起引用块——否则子行只剩
        # 孤零零一个厂商，跟在折行的主行后面很飘）。
        ent = entities.get(e.get("app_id")) or e.get("publisher")
        rk = f"现 #{e['cur_rank']} · " if e.get("cur_rank") else ""  # 收入涨跌的排名参照系
        tail = f" · 厂商 {ent}" if ent else ""
        lines.append(f"💰 **{e['name']}** {rk}收入 **{e['pct']:+.0f}%**（{_fmt_money(e['prev_revenue'])} → {_fmt_money(e['cur_revenue'])}）{tail}")
    return lines[:cap] if cap else lines


def _articles_suffix(app_articles: Optional[list]) -> str:
    """新品行后缀：附最多 2 篇微信文章链接；标题清掉会破坏 markdown 链接/分隔的字符。"""
    if not app_articles:
        return ""
    links = []
    for a in app_articles[:2]:
        title = (a.title or "").replace("[", "(").replace("]", ")").replace("|", "/")
        title = " ".join(title.split())  # 折叠换行/多空格
        links.append(f"[{title}]({a.link})")
    return "\n   📰 " + " | ".join(links)


_LATIN_NAME_RE = re.compile(r"[A-Za-z]")
# CJK 统一表意 + 扩展A / 日文假名 / 韩文音节——用于判定「非拉丁名」走长度门槛。
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def _name_matches(name: str, text: str) -> bool:
    """新品名 nm 是否「真的」出现在文章 text 里——比裸 `nm in text` 精准。

    三类裸 substring 的误挂/漏挂治理：
    - **拉丁名**（含 ASCII 字母、无 CJK）：词边界 + 大小写无关——"Last War" 不再命中
      "Last War**ning**"，且 "last war" 也能命中（原本大小写敏感会漏）。
    - **非拉丁名**（CJK / 假名 / 韩文等无分词脚本）：按非空白字符数设最小长度门槛
      （WECHAT_MATCH_MIN_NAME_LEN，默认 2），过滤"城""塔"这类单字通用名的泛滥误挂；
      达标者仍走 substring（多字通用名如"탑 로드"裸 substring 仍可能误挂——无分词
      解决不了，刻意不引停用词表，观察实际误挂率再定）。
    - 其余（纯符号/数字名等）：保守走 substring。
    """
    nm = (name or "").strip()
    if not nm or not text:
        return False
    has_latin = bool(_LATIN_NAME_RE.search(nm))
    has_cjk = bool(_CJK_RE.search(nm))
    if has_latin and not has_cjk:
        return re.search(r"\b" + re.escape(nm) + r"\b", text, re.IGNORECASE) is not None
    if has_cjk:
        if len(nm.replace(" ", "")) < settings.WECHAT_MATCH_MIN_NAME_LEN:
            return False
    return nm in text


def _match_articles_to_apps(per_combo: list[dict], article_list: list) -> dict:
    """搜到的文章 → 按「标题/摘要含新品名」聚合到 app_id：{app_id: [WechatArticle]}。

    用 (c.get("market") or {}) 而非 c.get("market", {})——entry 的 market/publisher
    初始为 None，后者在 key 存在时返回 None 会 AttributeError（曾导致整段静默失效）。

    名 ↔ 文匹配走 _name_matches（词边界 / 最小名长），治裸 substring 的短名/通用名
    误挂 + 拉丁名大小写漏挂。
    """
    name_to_apps: dict[str, list[str]] = {}
    for c in per_combo:
        rows = ((c.get("market") or {}).get("newcomers") or []) + \
               ((c.get("publisher") or {}).get("newcomers") or [])
        for n in rows:
            nm, aid = n.get("name"), n.get("app_id")
            if nm and aid:
                apps = name_to_apps.setdefault(nm, [])
                if aid not in apps:
                    apps.append(aid)
    out: dict[str, list] = {}
    for a in article_list:
        text = (a.title or "") + " " + (a.digest or "")
        for nm, app_ids in name_to_apps.items():
            if _name_matches(nm, text):
                for aid in app_ids:
                    out.setdefault(aid, []).append(a)
    return out


def build_newcomer_lines(market: dict, publisher: dict,
                         enrich: Optional[dict] = None,
                         articles: Optional[dict] = None,
                         entities: Optional[dict] = None,
                         country: Optional[str] = None,
                         platform: Optional[str] = None,
                         summaries: Optional[dict] = None) -> list[str]:
    """两层新品检测 → 人读行。
    enrich: {app_id: {genre, price, release_date}}
    articles: {app_id: [WechatArticle]} 微信公众号文章
    entities: {app_id: 中文厂商主体} —— 市场新面孔补中文归属（厂商新品行自带 entity_name）
    summaries: {app_id: 一句话中文摘要} —— LLM 中文化，让领导一眼看懂「这是什么游戏」
    country/platform: 该 combo 的市场坐标，用于给「新厂商待识别」线索行内拼商店页直达
    （缺省 None = 不拼链接，向后兼容老调用 / 单测）。

    **回归过滤**：`is_reentry=True` 的项不进 digest（老游戏跌出 baseline 又回来，
    标"新品"是误导）。is_reentry 字段在 no_baseline combo 里缺省，缺省 = False
    = 当真首发处理（与早期行为兼容）。先过滤再 [:10] 截断，避免被回归占满名额。
    """
    enrich = enrich or {}
    articles = articles or {}
    entities = entities or {}
    summaries = summaries or {}
    lines = []
    market_real = [n for n in (market.get("newcomers") or []) if not n.get("is_reentry")]
    for n in market_real[:10]:
        aid = n.get("app_id")
        is_lead = not n.get("is_slg")
        # #99 忽略名单过滤后，is_slg=false 多是「真新厂商线索」而非噪声——文案从单纯
        # 提示升级成带行动指引（建议建档），并行内附商店页直达（见下）。
        tag = "  ⚠️ 新厂商待识别 · 建议建档" if is_lead else ""
        en = enrich.get(aid) or {}
        meta = _meta_line(genre=en.get("genre"), revenue=n.get("revenue"),
                          downloads=n.get("downloads"),
                          entity=entities.get(aid) or n.get("publisher"))
        base = f"✨ **{n['name']}** 空降 **#{n['rank']}**{tag}" + meta
        if (s := summaries.get(aid)):   # LLM 一句话中文：领导一眼看懂这是什么游戏
            base += f"\n   📝 {s}"
        # 线索行内自带商店页链接：底部 ActionCard 按钮全局封顶 5 个、每 combo 只取 1 条，
        # 线索未必挤得进——行内链接让每条待识别线索都有「立即去看」入口。拼不出则只留文案。
        if is_lead and country and platform:
            url = _store_url(aid or "", country, platform)
            if url:
                base += f"\n   🔗 [查看商店页]({url})"
        # 看板定位深链：点进新品页高亮该 app（未配 DASHBOARD_BASE_URL 时省略）。
        focus = _dashboard_focus_url(aid or "", "market")
        if focus:
            base += f"\n   🎯 [看板定位]({focus})"
        base += _articles_suffix(articles.get(aid))
        lines.append(base)
    publisher_real = [n for n in (publisher.get("newcomers") or []) if not n.get("is_reentry")]
    for n in publisher_real[:10]:
        aid = n.get("app_id")
        rank = f"#{n['rank']}" if n.get("rank") else "进榜"
        meta = _meta_line(revenue=n.get("revenue"), downloads=n.get("downloads"))
        base = f"🏢 **{n['entity_name']}** 新品 **{n['name']}** {rank}" + meta
        if (s := summaries.get(aid)):
            base += f"\n   📝 {s}"
        focus = _dashboard_focus_url(aid or "", "publisher")
        if focus:
            base += f"\n   🎯 [看板定位]({focus})"
        base += _articles_suffix(articles.get(aid))
        lines.append(base)
    return lines


def build_free_newcomer_lines(market: dict, publisher: dict,
                              articles: Optional[dict] = None,
                              entities: Optional[dict] = None) -> list[str]:
    """下载榜新品 → 人读行（ADR 0001 切片 2）。

    **钉钉只推 is_slg=True**（下载榜噪声大：休闲/工具类装机榜混入多）——非 SLG 的
    下载榜新品仍照常入库 + 看板可见，只是不进钉钉卡片（口径差异是刻意的，见 ADR）。
    回归同样过滤。⬇️ 前缀与收入榜区分。市场+主体两路按 app_id 去重。
    """
    from app.services.slg_publishers import is_slg
    articles = articles or {}
    entities = entities or {}
    merged: dict[str, dict] = {}
    for n in (market.get("newcomers") or []):
        if n.get("is_slg") and not n.get("is_reentry"):
            merged[n["app_id"]] = n
    for n in (publisher.get("newcomers") or []):
        aid = n.get("app_id")
        if n.get("is_reentry") or aid in merged:
            continue
        if is_slg(aid, n.get("publisher")):  # 主体行也按 is_slg 门控（必须 SLG 才推）
            merged[aid] = n
    lines = []
    for n in list(merged.values())[:10]:
        aid = n.get("app_id")
        rank = f"#{n['rank']}" if n.get("rank") else "上榜"
        meta = _meta_line(downloads=n.get("downloads"),
                          entity=n.get("entity_name") or entities.get(aid) or n.get("publisher"))
        base = f"⬇️ **{n['name']}** 下载榜 **{rank}**" + meta
        focus = _dashboard_focus_url(aid or "", "market")
        if focus:
            base += f"\n   🎯 [看板定位]({focus})"
        base += _articles_suffix(articles.get(aid))
        lines.append(base)
    return lines


def build_lead_newcomer_lines(lead_items: list[dict]) -> list[str]:
    """下载榜 is_slg=false 但 genre=Strategy 的新品 → 「待建档新厂线索」行（方案①）。

    is_slg 白名单滞后维护，会把「未识别的真新厂」（典型如 LAST ORIGIN STUDIO /
    Last Shelter: War Z）挡在下载榜 SLG 推送门控（build_free_newcomer_lines）之外 →
    漏推给领导。这段把这类线索单列给维护者：人工核查后建档进白名单 → 该厂后续新品
    自动进 SLG 推送，形成「提醒 → 建档 → 不再漏」闭环。忽略名单已在 detect_newcomers
    滤过确认非 SLG，调用方再用 genre 初筛压掉休闲噪声（Puzzle/工具等）。封顶防刷屏。"""
    out: list[str] = []
    cap = settings.DIGEST_MAX_ITEMS
    seen: set[str] = set()
    for it in lead_items:
        aid = it.get("app_id")
        if not aid or aid in seen:
            continue
        seen.add(aid)
        rank = f"#{it['rank']}" if it.get("rank") else "上榜"
        mkt = _market_label(it.get("country", ""), it.get("platform", ""))
        pub = it.get("publisher") or "未知发行商"
        genre = it.get("genre") or ""
        suffix = f" · {genre}" if genre else ""
        line = f"🔍 **{it.get('name') or aid}**（{mkt} 下载榜 {rank}{suffix}）｜发行商：{pub}"
        focus = _dashboard_focus_url(aid, "market")
        if focus:
            line += f"\n   🎯 [看板核查]({focus})"
        out.append(line)
        if len(out) >= cap:
            break
    return out


def build_version_lines(changes: list[dict], cap: int) -> list[str]:
    """版本变更 → 人读行（需求② / ADR 0003）。changes: [{name, old, new, date}]。

    全局段（跨 combo），tracked iOS 竞品版本更新。封顶 cap 防极端日刷屏。
    """
    out: list[str] = []
    for c in changes[:cap]:
        date = f"（{c['date']}）" if c.get("date") else ""
        out.append(f"🆙 **{c['name']}**：{c['old']} → {c['new']}{date}")
    return out


def build_video_lines(items: list[dict], cap: int) -> list[str]:
    """新品实机视频 → 人读行（需求① / ADR 0002）。items: [{name, count, url}]。

    让领导在钉钉就看到「系统给新竞品自动搜了实机视频」，免开网站。url = 头条视频。
    封顶 cap 防刷屏。
    """
    out: list[str] = []
    for it in items[:cap]:
        link = f" [看第一条]({it['url']})" if it.get("url") else ""
        out.append(f"🎬 **{it['name']}**：已搜集 {it['count']} 条实机玩法视频{link}")
    return out


def build_region_launch_lines(changes: list[dict], cap: int) -> list[str]:
    """竞品新进某区 → 人读行（需求② 子项③ / ADR 0004）。changes: [{name, country, date}]。

    全局段，tracked iOS 竞品新上架的 storefront（扩区动作）。封顶 cap 防刷屏。
    """
    out: list[str] = []
    for c in changes[:cap]:
        date = f"（{c['date']}）" if c.get("date") else ""
        out.append(f"🌍 **{c['name']}**：新进 {c['country']} 区{date}")
    return out


def build_daily_digest(per_combo: list[dict], today: str,
                       articles: Optional[dict] = None,
                       entities: Optional[dict] = None,
                       version_changes: Optional[list[dict]] = None,
                       video_items: Optional[list[dict]] = None,
                       region_changes: Optional[list[dict]] = None,
                       summaries: Optional[dict] = None,
                       lead_items: Optional[list[dict]] = None) -> Optional[tuple[str, str, list[tuple[str, str]]]]:
    """全 combo 检测结果 → (title, markdown, btns)。全空 → None（不发）。

    per_combo: [{country, platform, movement: dict|None, market: dict|None, publisher: dict|None}]
    articles: {app_id: [WechatArticle]} 微信公众号文章（按 app_id 聚合）
    entities: {app_id: 中文厂商主体}（市场新面孔 / 异动行补中文归属）
    """
    sections: list[str] = []
    btns: list[tuple[str, str]] = []
    cap = settings.DIGEST_MAX_ITEMS
    mv_cap = settings.DIGEST_MOVEMENT_TOPN
    total = 0      # 全部检出项（含未展示），进标题
    shown = 0      # 已渲染项，触发全局封顶
    overflow = 0   # 因封顶/movement 截断未展示的项，进折叠行（不静默丢）
    for c in per_combo:
        mv_all = build_movement_lines(c["movement"], entities=entities) if c.get("movement") else []
        nc_blocks = (build_newcomer_lines(c.get("market") or {}, c.get("publisher") or {},
                                          enrich=c.get("enrich"), articles=articles,
                                          entities=entities, summaries=summaries,
                                          country=c["country"], platform=c["platform"])
                     if (c.get("market") or c.get("publisher")) else [])
        # 下载榜新品（ADR 0001 切片 2）：只推 is_slg=True，⬇️ 段单列。
        free_blocks = (build_free_newcomer_lines(c.get("free_market") or {},
                                                 c.get("free_publisher") or {},
                                                 articles=articles, entities=entities)
                       if (c.get("free_market") or c.get("free_publisher")) else [])
        if not mv_all and not nc_blocks and not free_blocks:
            continue
        total += len(mv_all) + len(nc_blocks) + len(free_blocks)
        if shown >= cap:
            overflow += len(mv_all) + len(nc_blocks) + len(free_blocks)  # 整 combo 封顶未展示
            continue
        mv_blocks = mv_all[:mv_cap]
        overflow += len(mv_all) - len(mv_blocks)       # movement 尾部超额（按重要性砍）
        shown += len(mv_blocks) + len(nc_blocks) + len(free_blocks)
        # 分组小标题（异动 / 收入榜新品 / 下载榜新品）让领导一眼分清。
        parts = [f"**{_combo_label(c['country'], c['platform'])}**"]
        if mv_blocks:
            parts.append("【榜单异动】\n\n" + "\n\n".join(mv_blocks))
        if nc_blocks:
            parts.append("【新品上架】\n\n" + "\n\n".join(nc_blocks))
        if free_blocks:
            parts.append("【下载榜新品 · SLG】\n\n" + "\n\n".join(free_blocks))
        sections.append("\n\n".join(parts))
        # 按钮：异动(空降/窜升) + 新品(市场/厂商) 各取头条直达商店页；全局最多 5、去重。
        # 新品也产出按钮——纯新品日不再无可点项；安卓包名拼 GP 链接（_store_url）。
        for e in (((c.get("movement") or {}).get("new_entrants") or [])[:1]
                  + ((c.get("movement") or {}).get("surges") or [])[:1]
                  + [n for n in ((c.get("market") or {}).get("newcomers") or []) if not n.get("is_reentry")][:1]
                  + [n for n in ((c.get("publisher") or {}).get("newcomers") or []) if not n.get("is_reentry")][:1]):
            url = _store_url(e.get("app_id", ""), c["country"], c["platform"])
            if url and len(btns) < 5 and all(b[1] != url for b in btns):
                btns.append((f"{e['name']} →", url))
    # 全局「版本更新」段（跨 combo，tracked iOS 竞品版本变更，ADR 0003 切片 B）。
    # 放 combo 段之后；纯版本更新日（无异动/新品）也能让 sections 非空、照常发卡。
    if version_changes:
        vlines = build_version_lines(version_changes, cap)
        if vlines:
            total += len(version_changes)
            sections.append("【版本更新 · iOS 竞品】\n\n" + "\n\n".join(vlines))
    # 全局「竞品新区上线」段（需求② 子项③ / ADR 0004）：tracked iOS 竞品新进某区。
    if region_changes:
        rlines = build_region_launch_lines(region_changes, cap)
        if rlines:
            total += len(region_changes)
            sections.append("【竞品新区上线 · iOS】\n\n" + "\n\n".join(rlines))
    # 全局「新品实机视频」段（需求① / ADR 0002）：今日新品已自动搜集的实机视频。
    if video_items:
        vid_lines = build_video_lines(video_items, cap)
        if vid_lines:
            total += len(video_items)
            sections.append("【新品实机视频】\n\n" + "\n\n".join(vid_lines))
    # 全局「待建档新厂线索」段（方案①）：下载榜 is_slg=false（白名单未收录）但
    # genre=Strategy 的新品，单列给维护者核查建档——补救白名单滞后导致的漏推（领导端
    # 的「下载榜新品 · SLG」段仍只推已确认 SLG，这段标注清楚是「待核查线索」不混淆）。
    if lead_items:
        lead_lines = build_lead_newcomer_lines(lead_items)
        if lead_lines:
            total += len(lead_lines)
            sections.append(
                "🔍 **待建档新厂线索**（下载榜疑似 SLG、白名单未收录 → 请人工核查建档）"
                "\n\n" + "\n\n".join(lead_lines))
    if not sections:
        return None
    head = f"### 📡 SLG 每日情报 · {today}（{total} 项）"
    body = [head] + sections
    if overflow:
        # 配了看板基址就把「看板查看全部」做成深链（落到新品页），否则纯文案。
        base = (settings.DASHBOARD_BASE_URL or "").rstrip("/")
        tail = f"[看板查看全部]({base}/newcomers)" if base else "看板查看全部"
        body.append(f"> …另有 **{overflow}** 项未在此展示，{tail}")
    return f"每日情报 {today}", "\n\n---\n\n".join(body), btns


async def send_daily_digest() -> bool:
    """日级 job 入口：对全部已配置 combo 重跑检测，拼一张卡发一次。

    只纳入当天有新快照的 combo：movement 靠 today_missing 闸门，新品靠
    as_of == today 闸门——次市场（周/月级同步）的旧快照不会被每天重报。
    """
    # 版本追踪先跑：落 game_histories(version 事件) + 更新 Game 当前值，独立于
    # webhook 配置（无 webhook 也积累历史）。USE_MOCK_DATA / 无 iOS games 时 no-op。
    # 返回的结构化变更（name/old/new/date）直接拼 digest「版本更新」段（ADR 0003）。
    from app.services.version_tracker import check_tracked_versions
    try:
        version_changes = await check_tracked_versions()
    except Exception:
        logger.exception("Version check (in digest) crashed")
        version_changes = []
    # 竞品「新进某区」事件（需求② 子项③ / ADR 0004）：与版本检测同样**放 webhook
    # 闸门之前**——落 game_histories(region_launch) 不依赖 webhook（无 webhook 也积累
    # 历史 + 详情页时间线），且避免事件因 30 天窗口过期而永久漏记。零 ST 纯本地表读。
    from app.services.region_launch import detect_new_region_launches
    try:
        region_changes = await detect_new_region_launches()
    except Exception:
        logger.exception("Region launch detection (in digest) crashed")
        region_changes = []
    # 新品中文化（LLM 网关）：给今日 is_slg 新品补 summary_cn/description_cn。放 webhook
    # 闸门**之前**——前端抽屉也要中文，不依赖 webhook。USE_MOCK_DATA/无 key 整体 no-op。
    from app.services.newcomer_i18n import translate_pending_newcomers
    try:
        await translate_pending_newcomers()
    except Exception:
        logger.exception("Newcomer translate (in digest) crashed")
    if not dingtalk.is_enabled():
        return False
    from app.services.movement import detect_movement
    from app.services.newcomers import (
        detect_newcomers, detect_publisher_newcomers,
        _load_ignore_keys, _load_entity_matchers, resolve_entity,
    )

    today = utcnow_naive().strftime("%Y-%m-%d")
    per_combo: list[dict] = []
    all_newcomer_names: set[str] = set()  # 收集所有新品名称，用于批量搜微信文章
    # 跨 combo 共享的两份只读数据，循环外预加载一次：忽略名单（市场新面孔过滤用）+
    # 主体归属匹配器（厂商新品归属 + 下方异动/市场行中文归属共用）。原本每 combo 各自
    # 重查（10 combo × ignores 1 + matchers 3 ≈ 40 次冗余小查询，matchers 还在末尾再加载
    # 一次），现降到各 1 次。
    ignore_keys = await _load_ignore_keys()
    matchers = await _load_entity_matchers()

    for country, platform in settings.sync_combos_list:
        entry: dict = {"country": country, "platform": platform, "movement": None,
                       "market": None, "publisher": None, "enrich": None,
                       "free_market": None, "free_publisher": None}
        try:
            m = await detect_movement(country, platform, today)
            if not m.get("today_missing"):
                entry["movement"] = m
            market = await detect_newcomers(country, platform, ignore_keys=ignore_keys)
            publisher = await detect_publisher_newcomers(country, platform, matchers=matchers)
            if market.get("as_of") == today:
                entry["market"] = market
                # 检出沉淀里的富化字段（record 已在同步路径先落库），给日报行加料
                ids = [n["app_id"] for n in (market.get("newcomers") or [])]
                if ids:
                    from app.models.newcomer import MarketNewcomerLog
                    async with AsyncSessionLocal() as db:
                        logs = (await db.execute(
                            select(MarketNewcomerLog).where(
                                MarketNewcomerLog.country == country,
                                MarketNewcomerLog.platform == platform,
                                MarketNewcomerLog.app_id.in_(ids),
                            )
                        )).scalars().all()
                    entry["enrich"] = {l.app_id: {
                        "genre": l.genre, "price": l.price, "release_date": l.release_date,
                    } for l in logs}
                # 收集新品名称用于搜文章
                for n in (market.get("newcomers") or []):
                    if n.get("name"):
                        all_newcomer_names.add(n["name"])
            if publisher.get("as_of") == today:
                entry["publisher"] = publisher
                # 收集厂商新品名称
                for n in (publisher.get("newcomers") or []):
                    if n.get("name"):
                        all_newcomer_names.add(n["name"])
            # 下载榜（ADR 0001 切片 2）：仅开了该 combo 的额外检测一轮 free 榜；只在
            # 当期榜 as_of==today 时纳入（与收入榜同闸门）。build_free_newcomer_lines
            # 再按 is_slg 门控钉钉推送。
            if (country, platform) in settings.free_chart_combos_set:
                f_market = await detect_newcomers(country, platform, ignore_keys=ignore_keys,
                                                  chart_type=CHART_FREE)
                f_publisher = await detect_publisher_newcomers(country, platform,
                                                               matchers=matchers,
                                                               chart_type=CHART_FREE)
                if f_market.get("as_of") == today:
                    entry["free_market"] = f_market
                    for n in (f_market.get("newcomers") or []):
                        if n.get("name"):
                            all_newcomer_names.add(n["name"])
                if f_publisher.get("as_of") == today:
                    entry["free_publisher"] = f_publisher
                    for n in (f_publisher.get("newcomers") or []):
                        if n.get("name"):
                            all_newcomer_names.add(n["name"])
        except Exception:
            logger.exception("daily digest detection failed for %s/%s", country, platform)
        per_combo.append(entry)

    # 批量搜索微信文章（用新品名 + 厂商名）
    articles_by_app: dict = {}
    if all_newcomer_names and settings.WECHAT_ENABLED and not settings.USE_MOCK_DATA:
        try:
            from app.services.wechat_articles import search_multi_keywords
            keywords = list(all_newcomer_names)[:settings.WECHAT_MAX_KEYWORDS]
            article_list = await search_multi_keywords(keywords, limit=20)
            articles_by_app = _match_articles_to_apps(per_combo, article_list)
        except Exception:
            logger.warning("wechat articles search failed", exc_info=True)

    # 厂商主体中文归属：复用循环外已加载的 matchers，解析市场新面孔 / 异动行涉及的
    # app_id。厂商新品行自带 entity_name，这里只补另两层（纯内存匹配，零查询/零配额）。
    entities_by_app: dict = {}
    try:
        for c in per_combo:
            mv = c.get("movement") or {}
            rows = list((c.get("market") or {}).get("newcomers") or [])
            for k in ("new_entrants", "surges", "drops", "revenue_spikes"):
                rows += mv.get(k) or []
            for n in rows:
                aid = n.get("app_id")
                if aid and aid not in entities_by_app:
                    name = resolve_entity(aid, n.get("publisher"), matchers)
                    if name:
                        entities_by_app[aid] = name
    except Exception:
        logger.warning("entity attribution for digest failed", exc_info=True)

    # 今日新品（非回归）app_id → 名：视频段 + 一句话摘要段共用。
    newcomer_apps: dict[str, str] = {}
    for c in per_combo:
        for key in ("market", "publisher", "free_market", "free_publisher"):
            for n in ((c.get(key) or {}).get("newcomers") or []):
                aid, nm = n.get("app_id"), n.get("name")
                if aid and nm and not n.get("is_reentry"):
                    newcomer_apps.setdefault(aid, nm)

    # 需求①：今日新品已自动搜集的实机视频（非隐藏）→「新品实机视频」段。
    video_items: list[dict] = []
    try:
        if newcomer_apps:
            from app.models.newcomer import NewcomerVideo
            async with AsyncSessionLocal() as db:
                vids = (await db.execute(
                    select(NewcomerVideo)
                    .where(NewcomerVideo.app_id.in_(list(newcomer_apps)),
                           NewcomerVideo.hidden_at.is_(None))
                    .order_by(NewcomerVideo.app_id, NewcomerVideo.rank.is_(None),
                              NewcomerVideo.rank, NewcomerVideo.id)
                )).scalars().all()
            by_app: dict[str, list] = {}
            for v in vids:
                by_app.setdefault(v.app_id, []).append(v)
            for aid, vlist in by_app.items():
                video_items.append({"name": newcomer_apps[aid], "count": len(vlist),
                                    "url": vlist[0].url})
    except Exception:
        logger.exception("Video items for digest failed")

    # 新品一句话中文摘要（LLM 已在上面翻好，这里取出给 digest 新品行）。
    summaries_by_app: dict[str, str] = {}
    try:
        if newcomer_apps:
            from app.models.newcomer import MarketNewcomerLog
            async with AsyncSessionLocal() as db:
                srows = (await db.execute(
                    select(MarketNewcomerLog.app_id, MarketNewcomerLog.summary_cn)
                    .where(MarketNewcomerLog.app_id.in_(list(newcomer_apps)),
                           MarketNewcomerLog.summary_cn.is_not(None))
                )).all()
            for aid, s in srows:
                summaries_by_app.setdefault(aid, s)
    except Exception:
        logger.exception("Newcomer summaries for digest failed")

    # 方案①「待建档新厂线索」：下载榜 is_slg=false（白名单未收录）但 genre=Strategy 的
    # 新品，单列给维护者核查建档（补救白名单滞后漏推，见 LAST ORIGIN STUDIO 案例）。
    # 忽略名单已在 detect_newcomers 滤过；这里查 free 行 genre 再压掉休闲噪声。零配额。
    lead_items: list[dict] = []
    try:
        cand: dict[str, dict] = {}
        for c in per_combo:
            for key in ("free_market", "free_publisher"):
                for n in ((c.get(key) or {}).get("newcomers") or []):
                    aid = n.get("app_id")
                    if (aid and not n.get("is_slg") and not n.get("is_reentry")
                            and aid not in cand):
                        cand[aid] = {"app_id": aid, "name": n.get("name"),
                                     "publisher": n.get("publisher"), "rank": n.get("rank"),
                                     "country": c["country"], "platform": c["platform"]}
        if cand:
            from app.models.newcomer import MarketNewcomerLog
            async with AsyncSessionLocal() as db:
                grows = (await db.execute(
                    select(MarketNewcomerLog.app_id, MarketNewcomerLog.genre)
                    .where(MarketNewcomerLog.app_id.in_(list(cand)),
                           MarketNewcomerLog.chart_type == CHART_FREE)
                )).all()
            genre_by_app = {aid: (g or "") for aid, g in grows}
            for aid, info in cand.items():
                if "strateg" in genre_by_app.get(aid, "").lower():
                    lead_items.append({**info, "genre": genre_by_app.get(aid, "")})
    except Exception:
        logger.exception("Lead newcomer candidates (digest) failed")

    msg = build_daily_digest(per_combo, today, articles=articles_by_app,
                             entities=entities_by_app, version_changes=version_changes,
                             video_items=video_items, region_changes=region_changes,
                             summaries=summaries_by_app, lead_items=lead_items)
    if msg is None:
        logger.info("daily digest: nothing to report for %s", today)
        return False
    title, text, btns = msg
    return await dingtalk.send_action_card(title, text, btns)


# ── 微信公众号登录过期提醒 ─────────────────────────────────────────────────

_WECHAT_RELOGIN_HINT = (
    "重新扫码登录：终端跑 `ssh -L 5050:127.0.0.1:5000 hk-prod`，"
    "再浏览器打开 http://localhost:5050/login.html 用微信扫码。"
)


def build_wechat_expiry_alert(status, now_ts: float, warn_days: int) -> Optional[tuple[str, str]]:
    """微信登录状态 → (title, markdown) 提醒，或 None（健康 / 服务连不上时不提醒）。

    status=None 表示 wechat-api 连不上——那是另一类问题，不误报「登录过期」。
    """
    if status is None:
        return None
    if not status.logged_in or status.is_expired:
        text = ("### ⚠️ 微信公众号登录已失效\n\n"
                "新品监测日报将**暂停附带行业文章**（其余情报照常）。\n\n" + _WECHAT_RELOGIN_HINT)
        return "微信公众号登录已失效", text
    if status.expire_time_ms:
        days_left = (status.expire_time_ms / 1000 - now_ts) / 86400
        if days_left <= warn_days:
            text = (f"### ⏰ 微信公众号登录将在约 {max(0, round(days_left))} 天后过期\n\n"
                    f"账号：{status.nickname or '—'}。请尽快重新扫码，避免日报断档。\n\n" + _WECHAT_RELOGIN_HINT)
            return "微信公众号登录即将过期", text
    return None


async def alert_wechat_login_if_needed() -> bool:
    """每日检查 wechat 登录状态，失效/将过期则推钉钉。未启用 / 未配 webhook → 不发。"""
    if not (settings.WECHAT_ENABLED and dingtalk.is_enabled()):
        return False
    from app.services.wechat_articles import get_login_status
    status = await get_login_status()
    built = build_wechat_expiry_alert(status, time.time(), settings.WECHAT_EXPIRY_WARN_DAYS)
    if not built:
        return False
    return await dingtalk.send_markdown(*built)


# ── 应用商店雷达（iOS + GP 清单 diff，每轮检出即推） ────────────────────────

def _sf_text(app) -> str:
    """可见区描述：软启动（无 us）时明示，这是最关键的一行情报。GP 行不适用。"""
    sfs = [s for s in (app.storefronts or "").split(",") if s]
    if not sfs or sfs == ["gp"]:
        return ""
    label = "/".join(s.upper() for s in sfs)
    return f" · ⚠️ 仅 {label} 可见（疑似软启动）" if "us" not in sfs else f" · 可见区 {label}"


def _platform_tag(app) -> str:
    # 平台用图标区分（🤖 安卓 / 🍎 iOS）。GP 开发者页是「开发者全量目录」，gl=us 只
    # 影响语言/货币、对逐国过滤很弱，故只能标「美区视角」（我们从美区查到的口径），
    # 不等于美区在架；iOS 侧由 itunes country 参数硬过滤，真实可见区由 _sf_text 输出
    # （可见区 US / ⚠️ 仅 JP 可见），此处不重复。
    return "🤖 Google Play · 美区视角" if (app.storefronts or "") == "gp" else "🍎 App Store"


def build_appstore_digest(
    rows: list[tuple],
    expanded: Optional[list[tuple]] = None,
) -> Optional[tuple[str, str, list[tuple[str, str]]]]:
    """rows: [(PublisherItunesApp, entity_name, artist_label)]；
    expanded: [(PublisherItunesApp, entity_name, added_storefronts)]
    → (title, markdown, btns)。"""
    expanded = expanded or []
    if not rows and not expanded:
        return None
    lines = ["### 🛒 SLG 商店雷达 · 重点厂商上新"]
    btns: list[tuple[str, str]] = []
    if rows:
        lines.append(f"**新上架（{len(rows)} 款）**")
        for app, entity_name, _label in rows[:15]:
            released = f" · 上架 {app.release_date}" if app.release_date else ""
            genre = f" · {app.genre}" if app.genre else ""
            lines.append(f"🆕 **{app.name}** — {entity_name}（{_platform_tag(app)}）"
                         f"{genre}{released}{_sf_text(app)}")
            if app.track_view_url and len(btns) < 5:
                btns.append((f"{app.name} →", app.track_view_url))
        if len(rows) > 15:
            lines.append(f"…等共 {len(rows)} 款，看板查看全部")
    if expanded:
        lines.append(f"**扩区上线（{len(expanded)} 款，软启动 → 更大范围）**")
        for app, entity_name, added in expanded[:15]:
            added_label = "/".join(s.upper() for s in added)
            now_label = "/".join(s.upper() for s in (app.storefronts or "").split(",") if s)
            lines.append(f"🌍 **{app.name}** — {entity_name} 新增 **{added_label}**（现 {now_label}）")
            if app.track_view_url and len(btns) < 5:
                btns.append((f"{app.name} →", app.track_view_url))
        if len(expanded) > 15:
            lines.append(f"…等共 {len(expanded)} 款")
    return "商店雷达上新", "\n\n".join(lines), btns


async def alert_appstore_releases(
    since: datetime,
    expanded: Optional[list[tuple[int, list[str]]]] = None,
) -> bool:
    """清单同步（iOS 或 GP 轮）后调用：推送本轮（first_seen_at >= since）的
    非基线新上架与扩区上线（expanded = [(app row id, 新增区列表)]）。"""
    if not dingtalk.is_enabled():
        return False
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(PublisherItunesApp, PublisherEntity.name, PublisherItunesArtist.label)
            .join(PublisherEntity, PublisherEntity.id == PublisherItunesApp.entity_id)
            .join(PublisherItunesArtist, PublisherItunesArtist.id == PublisherItunesApp.artist_row_id)
            .where(
                PublisherItunesApp.is_baseline.is_(False),
                PublisherItunesApp.first_seen_at >= since,
            )
            .order_by(PublisherItunesApp.id)
        )).all()

        expanded_rows: list[tuple] = []
        if expanded:
            added_by_id = {rid: added for rid, added in expanded}
            for app, entity_name in (await db.execute(
                select(PublisherItunesApp, PublisherEntity.name)
                .join(PublisherEntity, PublisherEntity.id == PublisherItunesApp.entity_id)
                .where(PublisherItunesApp.id.in_(list(added_by_id)))
                .order_by(PublisherItunesApp.id)
            )).all():
                expanded_rows.append((app, entity_name, added_by_id[app.id]))

    msg = build_appstore_digest(list(rows), expanded_rows)
    if msg is None:
        return False
    title, text, btns = msg
    return await dingtalk.send_action_card(title, text, btns)
