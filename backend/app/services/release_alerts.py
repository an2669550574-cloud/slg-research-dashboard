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


_MD_FMT_RE = re.compile(r"([\\`*_~])")  # 行内格式字符：反斜杠/代码/加粗斜体/删除线


def _md_name(s, maxlen: Optional[int] = 32) -> str:
    """**粗体/正文文本位**的名字净化——防 ST 原始游戏名/厂商名把卡片渲染破版：
    ① 折叠换行/多余空白 ② 超长截断（maxlen，None=不截）③ 方括号 → 圆括号（否则
    `名](url` 会误拼成链接）④ 转义 `* _ \\` `` ` `` `~`（否则触发加粗错位/代码块）。

    只用于 markdown **正文文本**插值（`**{name}**` / 厂商名 / 版本号等）。ActionCard
    按钮 title 是纯文本、不要过它；`[锚文本](url)` 里的文章标题另有 sanitize（只需括号
    替换、不转义格式符，见 `_link_line` / `_articles_suffix`）。ST 原始名几乎必然某天带
    `[Beta]` / `*` / 方括号，这一道是「推领导前最容易当面出丑」的防线。"""
    s = " ".join(str(s if s is not None else "").split())
    if maxlen and len(s) > maxlen:
        s = s[:maxlen - 1].rstrip() + "…"
    s = s.replace("[", "(").replace("]", ")")
    return _MD_FMT_RE.sub(r"\\\1", s)


def _market_label(country: str, platform: str) -> str:
    """市场+平台标识（不带榜种），如「🇺🇸 美国 · 安卓」。下载榜/跨段复用，避免
    `_combo_label` 的「畅销榜」后缀与下载榜语境打架。"""
    flag = _COMBO_FLAG.get(country.lower(), "")
    cc = _COUNTRY_CN.get(country.lower(), country.upper())
    pf = _PLATFORM_CN.get(platform.lower(), platform)
    return f"{flag} {cc} · {pf}".strip()


def _combo_label(country: str, platform: str) -> str:
    return f"{_market_label(country, platform)} 畅销榜"


def _meta_inner(*, genre=None, revenue=None, downloads=None, entity=None) -> str:
    """meta 子行的纯内容（无 markdown 前缀）：品类 · 日收入 · 下载 · 厂商。全空返回 ""。
    新品行用它拼独立引用段（见 _block）；movement 仍走 _meta_line 的行尾拼接。"""
    parts = []
    if (g := _genre_cn(genre)):
        parts.append(g)
    if revenue:
        parts.append(f"日收入 {_fmt_money(revenue)}")
    if downloads:
        parts.append(f"下载 {_fmt_num(downloads)}")
    if entity:
        parts.append(f"厂商 {_md_name(entity)}")
    return " · ".join(parts)


def _meta_line(*, genre=None, revenue=None, downloads=None, entity=None) -> str:
    """条目下方的中文富化子行（引用块）。movement 用；新品行改用 _meta_inner + 独立段。
    钉钉 markdown 引用块会把后续单 \\n 续行 lazy-continuation 吸进同段并折叠换行，故
    带后续续行（摘要/链接）的新品行**不能**用本函数拼，必须 `\\n\\n` 分段（见 _block）。"""
    inner = _meta_inner(genre=genre, revenue=revenue, downloads=downloads, entity=entity)
    return "\n> " + inner if inner else ""


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


def _block(parts) -> str:
    """新品行各「段」用 `\\n\\n` 分隔——钉钉 markdown 只认空行换行，单 `\\n` 续行会被
    折叠粘连（真机验证：摘要/链接全黏进 meta 引用块一坨）。过滤空段。"""
    return "\n\n".join(p for p in parts if p)


def _link_line(app_id: str, view: str, *, country=None, platform=None,
               with_store: bool = False, articles=None) -> str:
    """新品行的链接段，合并成一行（少续行 = 钉钉移动端更清爽）：
    🔗 商店页 · 🎯 看板，末尾接 📰 文章（≤2 篇）。全空返回 ""。"""
    segs = []
    if with_store and country and platform:
        if (url := _store_url(app_id or "", country, platform)):
            segs.append(f"💻 [商店页]({url})")   # 💻=外网链接，手机端打不开（见底部图例）
    if (focus := _dashboard_focus_url(app_id or "", view)):
        segs.append(f"🎯 [看板]({focus})")   # 看板自建·两端可达
    line = " · ".join(segs)
    arts = []
    for a in (articles or [])[:2]:
        title = (a.title or "").replace("[", "(").replace("]", ")").replace("|", "/")
        title = " ".join(title.split())
        arts.append(f"[{title}]({a.link})")
    if arts:
        line += (" · " if line else "") + "📰 " + " / ".join(arts)
    return line


# ── 重要度打分（统一喂给 排序 / 全局封顶 / movement TopN / 按钮 / 今日要闻 五处）──
# 此前这五处一律按 sync_combos_list 的**地理顺序**砍尾：次市场长尾能把核心市场的大
# 事件（头部空降、高名次收入异动）挤出卡片或按钮折叠。改成统一打分「市场权重 × 事件
# 强度」喂这五处——让最值得领导看的事件无论落在哪个 combo 都优先保留 + 置顶「今日要闻」。
#
# 权重是**产品判断**（无标注 ground truth）：美国是主市场（SLG 收入大盘）、iOS 收入盘子
# 略大于安卓。常态下该排序与现有地理顺序几乎一致（US→JP→KR、iOS→安卓），只有次市场冒出
# 真·大事件时才上浮——低惊扰。漏配市场/平台回退 1.0。
#
# 刻意**压窄区间**（1.0~1.5）：市场权重只做「轻微倾斜」，不能把事件强度（#1 空降 vs #45
# 长尾、+200% 收入异动 vs 微动）整个吃掉——否则「今日要闻」会被核心市场的榜尾长尾占满、
# 真·大事件反而沉底。而「核心 combo 永不被全局封顶挤掉」由 `_combo_sort_key` 的**主键**
# （市场权重，只需 US>次市场、与量级无关）保证，与此处区间大小解耦。
_MARKET_WEIGHT = {"us": 1.5, "cn": 1.2, "jp": 1.15, "kr": 1.1, "tw": 1.05, "de": 1.05, "gb": 1.05}
_PLATFORM_WEIGHT = {"ios": 1.0, "android": 0.9}
# movement 四类 → (kind, summary 字段名)，多处复用（行渲染排序 / 今日要闻收集）。
_MOVEMENT_KINDS = (("new_entrant", "new_entrants"), ("surge", "surges"),
                   ("drop", "drops"), ("revenue_spike", "revenue_spikes"))
# new_entrant 命中「回归」(is_reentry) 时的强度乘数：老 SLG 短暂跌出 TopN 又回来 ≠ 真首发，
# 新闻性远低，乘此系数压低分——既改文案「🔄 重回」又**降权今日要闻**（高名次回归仍可冒头，
# 不硬排除）。0.4 让 #1 回归(≈4.8) 仍高于榜尾长尾、却低于真·头部空降(≈12)/高名次收入异动。
_REENTRY_PENALTY = 0.4


def _market_weight(country: str, platform: str) -> float:
    return (_MARKET_WEIGHT.get((country or "").lower(), 1.0)
            * _PLATFORM_WEIGHT.get((platform or "").lower(), 1.0))


def _rank_height(rank, topn: int = 50) -> float:
    """名次「高度」0..1：越靠榜首越大（#1→~1.0，#topn→~0，榜外/缺省→0）。给「事件落点
    多靠前」一个连续权重——领导更关心头部异动，榜尾抖动次要。"""
    if not rank or rank <= 0:
        return 0.0
    return max(0.0, (topn - rank + 1) / topn)


def _event_score(kind: str, e: dict) -> float:
    """单事件「强度」分（不含市场权重，故可直接用于 combo 内 movement 排序）。分档拍定
    后用真实样例校准过相对序：高名次收入异动 > 头部空降/市场新品 > 大幅窜升 > 榜尾长尾
    空降/跌出（见 test_digest_importance_*）。缺字段一律退化为低分、不抛。"""
    if kind == "new_entrant":
        base = 6 + 6 * _rank_height(e.get("cur_rank"))
        return base * _REENTRY_PENALTY if e.get("is_reentry") else base
    if kind == "surge":
        jump = max(0, (e.get("prev_rank") or 0) - (e.get("cur_rank") or 0))
        return 3 + 4 * _rank_height(e.get("cur_rank")) + min(jump, 40) * 0.15
    if kind == "drop":
        return 2 + 4 * _rank_height(e.get("prev_rank"))
    if kind == "revenue_spike":
        return 5 + 5 * _rank_height(e.get("cur_rank")) + min(abs(e.get("pct") or 0), 200) * 0.04
    if kind == "market_newcomer":
        return 6 + 6 * _rank_height(e.get("rank"))
    if kind == "free_newcomer":
        return 4 + 5 * _rank_height(e.get("rank"))
    if kind == "publisher_newcomer":
        return 4 + 4 * _rank_height(e.get("rank"), topn=settings.PUBLISHER_NEWCOMER_TOPN)
    return 1.0


# ── 同赛道：这竞品和我方哪款产品同赛道（决策锚点，纯本地零 ST/零 LLM）──────────
# digest 现只有竞品 name/rank/revenue，不告诉领导「这竞品和我方哪款同赛道」=最大决策缺口。
# **优先按玩法子品类精确匹配**（own_products.match_subgenre vs 竞品 LLM 分类 subgenre_cn）——
# 题材关键词（末日/丧尸）横跨数字门/基地建设/塔防多品类，先天分不出真同赛道；子品类按核心机制
# 分类才区分得开。未配子品类的产品回退题材关键词子串匹配。命中给行尾打「⚔️《本品》同赛道」+
# TL;DR 计数，让领导一眼看出「要不要管」。

# 命中「同赛道」的竞品在「今日要闻」里的重要度乘数：竞品打进我方赛道 = 对领导的第一决策轴，
# 比随机高名次次市场新品更要紧。强度分 × 市场权重 × 本系数后参与跨 combo 排序，确保
# 同赛道竞品不被长尾挤出今日要闻。仅影响今日要闻排序（标签 ⚔️ 仍照常打）。
_OWN_MATCH_BOOST = 2.5


def _match_own_product(text: str, subgenre: Optional[str],
                       products: list[tuple[str, list[str], set[str]]]) -> Optional[tuple[str, str]]:
    """竞品 (文本, 玩法子品类 subgenre_cn) → 命中的我方产品 (产品名, 命中依据)，第一个命中即返回。
    **子品类优先**：产品配了 match_subgenre 就**只**按子品类相等匹配（精确，忽略关键词，治题材
    关键词太宽泛）；没配子品类才回退题材关键词小写子串匹配。竞品无子品类（未分类/老行）+ 产品
    只认子品类 → 不命中（宁缺毋滥，正是要去掉的假阳）。"""
    t = (text or "").lower()
    sg = (subgenre or "").strip()
    for name, kws, subs in products:
        if subs:  # 配了子品类 → 子品类相等权威匹配
            if sg and sg in subs:
                return (name, sg)
            continue   # 该产品只认子品类，不回退关键词（避免题材泛匹配漏回来）
        for kw in kws:  # 未配子品类 → 回退题材关键词
            if kw and t and kw in t:
                return (name, kw)
    return None


def _own_tag(app_id, own_matches: Optional[dict]) -> str:
    """竞品行尾「同赛道」标签：命中 → ` ⚔️《X》同赛道`，否则 ``。⚔️ 刻意避开已表
    「看板」的 🎯。产品名过 _md_name 防破版。"""
    name = (own_matches or {}).get(app_id)
    return f" ⚔️《{_md_name(name, maxlen=20)}》同赛道" if name else ""


async def _load_own_products() -> list[tuple[str, list[str], set[str]]]:
    """我方产品 (name, [小写关键词], {玩法子品类}) ——「同赛道」匹配用。match_keywords 与
    match_subgenre **都空**的产品跳过。按 is_default 优先、id 次之排序，命中多款时取确定性首个。"""
    from app.models.product import OwnProduct
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(OwnProduct.name, OwnProduct.match_keywords, OwnProduct.match_subgenre)
            .order_by(OwnProduct.is_default.desc(), OwnProduct.id)
        )).all()
    out: list[tuple[str, list[str], set[str]]] = []
    for name, kw, sg in rows:
        kws = [k.strip().lower() for k in (kw or "").split(",") if k.strip()]
        subs = {s.strip() for s in (sg or "").split(",") if s.strip()}
        if kws or subs:
            out.append((name, kws, subs))
    return out


# ── 每日情报汇总（竞品异动 + 两层新品，全 combo 一条） ─────────────────────

def build_movement_lines(s: dict, entities: Optional[dict] = None,
                         cap: Optional[int] = None,
                         own_matches: Optional[dict] = None) -> list[str]:
    """movement 摘要 → 人读行，**按重要度降序**（同分稳定保序）。与 Sentry 的
    [NEW]/[UP] 机器码刻意分离。
    entities: {app_id: 中文厂商主体} —— 给每条补「日收入 · 下载 · 厂商归属」子行。
    own_matches: {app_id: 我方产品名} —— 命中则行尾打「⚔️《X》同赛道」（对标我方哪款）。
    cap: 单 combo 展示行上限（按 `_event_score` 砍掉重要性较低的尾部，不再按
    空降/窜升/暴跌/收入异动 的固定类序砍——否则末类的大额收入异动会被前类长尾挤掉），
    None=不限。combo 内市场权重恒定，故只按事件强度排序即与全卡口径一致。"""
    entities = entities or {}

    def _meta(e):
        return _meta_line(revenue=e.get("revenue"), downloads=e.get("downloads"),
                          entity=entities.get(e.get("app_id")) or e.get("publisher"))

    def _tag(e):
        return _own_tag(e.get("app_id"), own_matches)

    scored: list[tuple[float, str]] = []
    for e in s["new_entrants"]:
        frm = "榜外" if e["prev_rank"] is None else f"#{e['prev_rank']}"
        # 回归（is_reentry）：老游戏短暂跌出 TopN 又回来，文案「🔄 重回」区别真「🆕 空降」。
        ico, verb = ("🔄", "重回") if e.get("is_reentry") else ("🆕", "空降")
        scored.append((_event_score("new_entrant", e),
                       f"{ico} **{_md_name(e['name'])}** {verb} **#{e['cur_rank']}**（{frm} →）" + _tag(e) + _meta(e)))
    for e in s["surges"]:
        scored.append((_event_score("surge", e),
                       f"📈 **{_md_name(e['name'])}** #{e['prev_rank']} → **#{e['cur_rank']}**（↑{e['prev_rank'] - e['cur_rank']}）" + _tag(e) + _meta(e)))
    for e in s["drops"]:
        to = "榜外" if e["cur_rank"] is None else f"#{e['cur_rank']}"
        scored.append((_event_score("drop", e),
                       f"📉 **{_md_name(e['name'])}** 跌出 Top 榜（#{e['prev_rank']} → {to}）" + _tag(e) + _meta(e)))
    for e in s["revenue_spikes"]:
        # 收入异动主行已带前后金额，厂商归属**内联行尾**（不另起引用块——否则子行只剩
        # 孤零零一个厂商，跟在折行的主行后面很飘）。
        ent = entities.get(e.get("app_id")) or e.get("publisher")
        rk = f"现 #{e['cur_rank']} · " if e.get("cur_rank") else ""  # 收入涨跌的排名参照系
        tail = f" · 厂商 {_md_name(ent)}" if ent else ""
        scored.append((_event_score("revenue_spike", e),
                       f"💰 **{_md_name(e['name'])}** {rk}收入 **{e['pct']:+.0f}%**（{_fmt_money(e['prev_revenue'])} → {_fmt_money(e['cur_revenue'])}）{tail}" + _tag(e)))
    scored.sort(key=lambda x: x[0], reverse=True)   # 稳定排序：同分保持类内原序
    lines = [ln for _, ln in scored]
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
                         summaries: Optional[dict] = None,
                         lead_cta: bool = True,
                         own_matches: Optional[dict] = None) -> list[str]:
    """两层新品检测 → 人读行。
    enrich: {app_id: {genre, price, release_date}}
    articles: {app_id: [WechatArticle]} 微信公众号文章
    entities: {app_id: 中文厂商主体} —— 市场新面孔补中文归属（厂商新品行自带 entity_name）
    summaries: {app_id: 一句话中文摘要} —— LLM 中文化，让领导一眼看懂「这是什么游戏」
    own_matches: {app_id: 我方产品名} —— 命中则标题行尾打「⚔️《X》同赛道」。
    country/platform: 该 combo 的市场坐标，用于给「新厂商待识别」线索行内拼商店页直达
    （缺省 None = 不拼链接，向后兼容老调用 / 单测）。
    lead_cta: is_slg=false 线索行是否带「建议建档」尾标 + 商店页直达。默认 True（维护者卡）；
    领导卡传 False——「建档」是维护者动作、对领导是杂讯，剥掉后该行退回纯「新对手上架」。

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
    # 「待识别新厂」(is_slg=false) 限量 + 折叠：次市场批量同步日会一次涌进几十个未识别新面孔
    # （混足球/塔防/恐怖等非 SLG 噪声，genre 仅本地化大类无法精准门控）。只详列前
    # DIGEST_MARKET_LEAD_TOPN 个（按榜排名），其余折叠成一行——建档线索仍可经折叠行→看板追溯，
    # 不静默丢。已识别 SLG（is_slg=true）不受此限（数量少 + 是核心情报，且多被 publisher 层覆盖）。
    lead_total = sum(1 for n in market_real if not n.get("is_slg"))
    lead_shown = 0
    for n in market_real[:10]:
        aid = n.get("app_id")
        is_lead_row = not n.get("is_slg")
        if is_lead_row:
            if lead_shown >= settings.DIGEST_MARKET_LEAD_TOPN:
                continue   # 超额待识别新厂：不逐条列，循环后统一折叠（见下方 lead_hidden）
            lead_shown += 1
        # #99 忽略名单过滤后，is_slg=false 多是「真新厂商线索」而非噪声——维护者卡升级文案带
        # 行动指引（建议建档）+ 行内商店页直达；领导卡 lead_cta=False 剥掉这套维护者动作。
        is_lead = is_lead_row and lead_cta
        tag = "  ⚠️ 新厂商待识别 · 建议建档" if is_lead else ""
        en = enrich.get(aid) or {}
        inner = _meta_inner(genre=en.get("genre"), revenue=n.get("revenue"),
                            downloads=n.get("downloads"),
                            entity=entities.get(aid) or n.get("publisher"))
        lines.append(_block([
            f"✨ **{_md_name(n['name'])}** 空降 **#{n['rank']}**{tag}{_own_tag(aid, own_matches)}",
            f"> {inner}" if inner else "",
            f"📝 {summaries.get(aid)}" if summaries.get(aid) else "",   # LLM 一句话：领导秒懂
            _link_line(aid or "", "market", country=country, platform=platform,
                       with_store=is_lead, articles=articles.get(aid)),
        ]))
    lead_hidden = lead_total - lead_shown
    if lead_hidden > 0:
        base = (settings.DASHBOARD_BASE_URL or "").rstrip("/")
        tail = f"，[看板核查]({base}/newcomers)" if base else ""   # 看板深链手机可达，不标 💻
        lines.append(f"> …另有 **{lead_hidden}** 个未识别新面孔上榜{tail}")
    publisher_real = [n for n in (publisher.get("newcomers") or []) if not n.get("is_reentry")]
    for n in publisher_real[:10]:
        aid = n.get("app_id")
        rank = f"#{n['rank']}" if n.get("rank") else "进榜"
        inner = _meta_inner(revenue=n.get("revenue"), downloads=n.get("downloads"))
        lines.append(_block([
            f"🏢 **{_md_name(n['entity_name'])}** 新品 **{_md_name(n['name'])}** {rank}{_own_tag(aid, own_matches)}",
            f"> {inner}" if inner else "",
            f"📝 {summaries.get(aid)}" if summaries.get(aid) else "",
            _link_line(aid or "", "publisher", articles=articles.get(aid)),
        ]))
    return lines


def build_free_newcomer_lines(market: dict, publisher: dict,
                              articles: Optional[dict] = None,
                              entities: Optional[dict] = None,
                              own_matches: Optional[dict] = None) -> list[str]:
    """下载榜新品 → 人读行（ADR 0001 切片 2）。

    **钉钉只推 is_slg=True**（下载榜噪声大：休闲/工具类装机榜混入多）——非 SLG 的
    下载榜新品仍照常入库 + 看板可见，只是不进钉钉卡片（口径差异是刻意的，见 ADR）。
    回归同样过滤。⬇️ 前缀与收入榜区分。市场+主体两路按 app_id 去重。
    own_matches: {app_id: 我方产品名} —— 命中则行尾打「⚔️《X》同赛道」。
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
        inner = _meta_inner(downloads=n.get("downloads"),
                            entity=n.get("entity_name") or entities.get(aid) or n.get("publisher"))
        lines.append(_block([
            f"⬇️ **{_md_name(n['name'])}** 下载榜 **{rank}**{_own_tag(aid, own_matches)}",
            f"> {inner}" if inner else "",
            _link_line(aid or "", "market", articles=articles.get(aid)),
        ]))
    return lines


def build_lead_newcomer_lines(lead_items: list[dict]) -> list[str]:
    """下载榜 is_slg=false 但 genre=Strategy 的新品 → 「待建档新厂线索」行（方案①）。

    is_slg 白名单滞后维护，会把「未识别的真新厂」（典型如 LAST ORIGIN STUDIO /
    Last Shelter: War Z）挡在下载榜 SLG 推送门控（build_free_newcomer_lines）之外 →
    漏推给领导。这段把这类线索单列给维护者：人工核查后建档进白名单 → 该厂后续新品
    自动进 SLG 推送，形成「提醒 → 建档 → 不再漏」闭环。忽略名单已在 detect_newcomers
    滤过确认非 SLG，调用方再用 genre 初筛压掉休闲噪声（Puzzle/工具等）。封顶防刷屏。

    **可读性（接 #147）**：genre 走 `_genre_cn` 转中文；`summary_cn`（#147 已把中文化
    扩到 is_slg=false 待识别新厂）有则补 📝 一句话——这段是 #147 把待识别在 UI 默认收起
    后维护者唯一的建档触点（钉钉日推），最该一眼看懂「这是什么游戏、要不要建档」。
    译文未就位（当日 cap 未轮到）时优雅降级、不显 📝。"""
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
        genre = _genre_cn(it.get("genre")) or ""   # 英文 genre → 中文
        suffix = f" · {genre}" if genre else ""
        summary = it.get("summary_cn")             # #147 待识别新厂中文化，这里接上
        focus = _dashboard_focus_url(aid, "market")
        out.append(_block([
            f"🔍 **{_md_name(it.get('name') or aid)}**（{mkt} 下载榜 {rank}{suffix}）",
            f"> 发行商 {_md_name(pub)}",
            f"📝 {summary}" if summary else "",
            f"🎯 [看板核查]({focus})" if focus else "",
        ]))
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
        out.append(f"🆙 **{_md_name(c['name'])}**：{_md_name(c['old'], maxlen=None)} → {_md_name(c['new'], maxlen=None)}{date}")
    return out


def build_video_lines(items: list[dict], cap: int) -> list[str]:
    """新品实机视频 → 人读行（需求① / ADR 0002）。items: [{name, count, url}]。

    让领导在钉钉就看到「系统给新竞品自动搜了实机视频」，免开网站。url = 头条视频。
    只详列前 cap 条，其余折叠成一行汇总（新品多的日子能搜出几十条，逐条列会刷屏）。
    """
    out: list[str] = []
    for it in items[:cap]:
        link = f" 💻 [看第一条]({it['url']})" if it.get("url") else ""   # YouTube=外网，手机打不开
        out.append(f"🎬 **{_md_name(it['name'])}**：已搜集 {it['count']} 条实机玩法视频{link}")
    extra = len(items) - cap
    if extra > 0:
        base = (settings.DASHBOARD_BASE_URL or "").rstrip("/")
        tail = f"，[看板查看全部]({base}/newcomers)" if base else ""   # 看板深链手机可达，不标 💻
        out.append(f"> …另有 **{extra}** 个新品也已搜集实机视频{tail}")
    return out


def build_region_launch_lines(changes: list[dict], cap: int) -> list[str]:
    """竞品新进某区 → 人读行（需求② 子项③ / ADR 0004）。changes: [{name, country, date}]。

    全局段，tracked iOS 竞品新上架的 storefront（扩区动作）。封顶 cap 防刷屏。
    """
    out: list[str] = []
    for c in changes[:cap]:
        date = f"（{c['date']}）" if c.get("date") else ""
        out.append(f"🌍 **{_md_name(c['name'])}**：新进 {c['country']} 区{date}")
    return out


def _digest_tldr(per_combo: list[dict], version_changes, region_changes,
                 video_items, lead_items, own_match_count: int = 0) -> str:
    """开头一句话总览（TL;DR）：让领导打开卡片先有「今天整体什么情况」的锚点，不用读完
    全卡才判断。新品按 app_id 去重跨榜/combo（市场+厂商+下载榜同一 app 只算一次）。
    own_match_count：命中「对标我方哪款」的竞品数——正向锚点，放最前让领导先看威胁面。"""
    move = 0
    new_apps: set = set()
    for c in per_combo:
        mv = c.get("movement") or {}
        move += sum(len(mv.get(k) or []) for k in
                    ("new_entrants", "surges", "drops", "revenue_spikes"))
        for key in ("market", "publisher", "free_market", "free_publisher"):
            for x in ((c.get(key) or {}).get("newcomers") or []):
                if not x.get("is_reentry") and x.get("app_id"):
                    new_apps.add(x["app_id"])
    bits = []
    if own_match_count:
        bits.append(f"⚔️ 同赛道 {own_match_count}")   # 直接威胁我方产品的竞品数，置顶
    if move:
        bits.append(f"📊 异动 {move}")
    if new_apps:
        bits.append(f"✨ 新品 {len(new_apps)}")
    if version_changes:
        bits.append(f"🆙 版本 {len(version_changes)}")
    if region_changes:
        bits.append(f"🌍 新区 {len(region_changes)}")
    if video_items:
        bits.append(f"🎬 视频 {len(video_items)}")
    if lead_items:
        bits.append(f"🔍 待建档 {len(lead_items)}")
    return " · ".join(bits)


def _collect_scored_items(per_combo: list[dict],
                          own_matches: Optional[dict] = None) -> list[tuple[float, dict]]:
    """全 combo 的可置顶事件 → [(score, item)] 按 score 降序。score = 事件强度 × 市场
    权重（× 对标加权）；item = {kind, e, country, platform}，供「今日要闻」渲染与按钮排序复用。
    回归项（is_reentry）已过滤；下载榜只算 is_slg=True（与 build_free_newcomer_lines
    推送门控一致）。命中 own_matches（对标我方）的竞品 × `_OWN_MATCH_BOOST` 上浮。
    这是「五处共用一个打分函数」里跨 combo 的那份。"""
    from app.services.slg_publishers import is_slg
    out: list[tuple[float, dict]] = []

    def add(kind, e, country, platform):
        score = _event_score(kind, e) * _market_weight(country, platform)
        if own_matches and e.get("app_id") in own_matches:
            score *= _OWN_MATCH_BOOST   # 对标我方的竞品上浮——打进我方赛道是领导第一决策轴
        out.append((score, {"kind": kind, "e": e, "country": country, "platform": platform}))

    for c in per_combo:
        country, platform = c["country"], c["platform"]
        mv = c.get("movement") or {}
        for kind, key in _MOVEMENT_KINDS:
            for e in mv.get(key) or []:
                add(kind, e, country, platform)
        for e in (c.get("market") or {}).get("newcomers") or []:
            if not e.get("is_reentry"):
                add("market_newcomer", e, country, platform)
        for e in (c.get("publisher") or {}).get("newcomers") or []:
            if not e.get("is_reentry"):
                add("publisher_newcomer", e, country, platform)
        for key in ("free_market", "free_publisher"):
            for e in (c.get(key) or {}).get("newcomers") or []:
                if e.get("is_reentry"):
                    continue
                slg = e.get("is_slg") if key == "free_market" else is_slg(e.get("app_id"), e.get("publisher"))
                if slg:
                    add("free_newcomer", e, country, platform)
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def _highlight_line(item: dict, own_matches: Optional[dict] = None) -> str:
    """「今日要闻」的一行紧凑摘要：跨 combo 置顶，故**内联市场标签**、去富化子行/链接，
    一眼看清「哪个市场、什么游戏、什么事」。命中对标则行尾打「⚔️《X》同赛道」。"""
    e = item["e"]
    mkt = _market_label(item["country"], item["platform"])
    kind = item["kind"]
    own = _own_tag(e.get("app_id"), own_matches)
    if kind == "new_entrant":
        ico, verb = ("🔄", "重回") if e.get("is_reentry") else ("🆕", "空降")
        return f"{mkt} {ico} **{_md_name(e['name'])}** {verb} #{e['cur_rank']}{own}"
    if kind == "surge":
        return f"{mkt} 📈 **{_md_name(e['name'])}** #{e['prev_rank']} → #{e['cur_rank']}{own}"
    if kind == "drop":
        to = "榜外" if e.get("cur_rank") is None else f"#{e['cur_rank']}"
        return f"{mkt} 📉 **{_md_name(e['name'])}** 跌出 Top（#{e['prev_rank']} → {to}）{own}"
    if kind == "revenue_spike":
        rk = f"#{e['cur_rank']} · " if e.get("cur_rank") else ""
        return f"{mkt} 💰 **{_md_name(e['name'])}** {rk}收入 {e['pct']:+.0f}%{own}"
    # 三类新品（market / publisher / free）：厂商新品用 entity_name，其余用 name
    nm = e.get("name") or e.get("entity_name") or "—"
    rk = f" #{e['rank']}" if e.get("rank") else ""
    return f"{mkt} ✨ **{_md_name(nm)}**{rk}{own}"


def build_highlight_lines(per_combo: list[dict], topn: int,
                          own_matches: Optional[dict] = None) -> list[str]:
    """跨 combo「今日要闻」Top N（重要度置顶）。topn<=0 或事件数 ≤ topn → 返回 []
    （小卡本身已短，置顶会和正文重复，没必要）。own_matches 既参与打分（对标竞品上浮）
    又用于渲染 ⚔️ 标签。"""
    if topn <= 0:
        return []
    scored = _collect_scored_items(per_combo, own_matches)
    if len(scored) <= topn:
        return []
    return [_highlight_line(item, own_matches) for _, item in scored[:topn]]


def _combo_sort_key(c: dict) -> tuple[float, float]:
    """combo 排序键（降序）：市场权重为主、combo 内最高单项强度为辅。让核心市场（US/iOS）
    稳居前列、永不被次市场长尾的全局封顶挤掉；同权重市场里有大事件的 combo 上浮。"""
    mw = _market_weight(c.get("country", ""), c.get("platform", ""))
    mv = c.get("movement") or {}
    best = 0.0
    for kind, key in _MOVEMENT_KINDS:
        for e in mv.get(key) or []:
            best = max(best, _event_score(kind, e))
    for key in ("market", "publisher", "free_market", "free_publisher"):
        for e in (c.get(key) or {}).get("newcomers") or []:
            if e.get("is_reentry"):
                continue
            k = {"market": "market_newcomer", "publisher": "publisher_newcomer"}.get(key, "free_newcomer")
            best = max(best, _event_score(k, e))
    return (mw, best)


def _ranked_newcomer_buttons(per_combo: list[dict]) -> list[tuple[str, str]]:
    """商店按钮（最多 5）：全 combo 的市场/厂商新品按重要度排序取头部 → 看板深链。
    此前按 combo 地理顺序各取头条，次市场的高价值新品永远排不进 5 个名额；改成全局
    按 `_event_score × 市场权重` 排序。movement 不进按钮（异动老游戏看板新品页定位不到）。
    未配 DASHBOARD_BASE_URL → 无深链 → 空列表（ActionCard 自动降级 markdown）。"""
    cands: list[tuple[float, dict]] = []
    for c in per_combo:
        mw = _market_weight(c["country"], c["platform"])
        for key, kind, view in (("market", "market_newcomer", "market"),
                                 ("publisher", "publisher_newcomer", "publisher")):
            for e in (c.get(key) or {}).get("newcomers") or []:
                if not e.get("is_reentry") and e.get("app_id"):
                    cands.append((_event_score(kind, e) * mw, {"e": e, "view": view}))
    cands.sort(key=lambda x: x[0], reverse=True)
    btns: list[tuple[str, str]] = []
    for _, cand in cands:
        url = _dashboard_focus_url(cand["e"].get("app_id", ""), cand["view"])
        if url and len(btns) < 5 and all(b[1] != url for b in btns):
            btns.append((f"{cand['e']['name']} →", url))
    return btns


def build_daily_digest(per_combo: list[dict], today: str,
                       articles: Optional[dict] = None,
                       entities: Optional[dict] = None,
                       version_changes: Optional[list[dict]] = None,
                       video_items: Optional[list[dict]] = None,
                       region_changes: Optional[list[dict]] = None,
                       summaries: Optional[dict] = None,
                       lead_items: Optional[list[dict]] = None,
                       audience: str = "maintainer",
                       own_matches: Optional[dict] = None) -> Optional[tuple[str, str, list[tuple[str, str]]]]:
    """全 combo 检测结果 → (title, markdown, btns)。全空 → None（不发）。

    per_combo: [{country, platform, movement: dict|None, market: dict|None, publisher: dict|None}]
    articles: {app_id: [WechatArticle]} 微信公众号文章（按 app_id 聚合）
    entities: {app_id: 中文厂商主体}（市场新面孔 / 异动行补中文归属）
    own_matches: {app_id: 我方产品名}（对标我方哪款，命中给该竞品行打「⚔️《X》同赛道」+ TL;DR 计数）
    audience: "maintainer"（默认，全量卡）/ "leader"（领导卡）。领导卡剥离维护者杂讯：
    跳过「待建档新厂线索」整段、新品行不拼「建议建档」尾标、TL;DR 不计「待建档 N」。
    检测数据两个 audience 共用一份（send_daily_digest 渲染两遍），零额外 ST。
    """
    is_leader = audience == "leader"
    # 领导卡口径「只看 SLG 产品」：剥离 market 层「待识别新厂」(is_slg=false——含足球/塔防/
    # 恐怖等非 SLG 噪声 + 白名单未收录的真新厂线索)。is_slg 是厂商维度，已识别 SLG 厂的
    # publisher 新品 + free(已 is_slg 门控) 保留。在此**一处**过滤，正文/TL;DR/今日要闻/按钮
    # 所有出口统一从过滤后 per_combo 取，避免逐出口判漏。维护者卡(全量 + 建档线索)不过滤。
    # 不 mutate 入参（浅拷副本：换掉 combo 的 market.newcomers，原入参不动）。
    if is_leader:
        _filtered = []
        for _c in per_combo:
            _mk = _c.get("market")
            if _mk and _mk.get("newcomers"):
                _c = {**_c, "market": {**_mk,
                      "newcomers": [n for n in _mk["newcomers"] if n.get("is_slg")]}}
            _filtered.append(_c)
        per_combo = _filtered
    sections: list[str] = []
    cap = settings.DIGEST_MAX_ITEMS
    mv_cap = settings.DIGEST_MOVEMENT_TOPN
    total = 0      # 全部检出项（含未展示），进标题
    shown = 0      # 已渲染项，触发全局封顶
    overflow = 0   # 因封顶/movement 截断未展示的项，进折叠行（不静默丢）
    # combo 按重要度排序（市场权重为主）：全局封顶砍的是真·次要 combo，核心 US/iOS 永不
    # 被次市场长尾挤折叠。原列表不变（不 mutate 入参），按钮另走全局重要度排序。
    ordered = sorted(per_combo, key=_combo_sort_key, reverse=True)
    for c in ordered:
        mv_all = (build_movement_lines(c["movement"], entities=entities, own_matches=own_matches)
                  if c.get("movement") else [])
        nc_blocks = (build_newcomer_lines(c.get("market") or {}, c.get("publisher") or {},
                                          enrich=c.get("enrich"), articles=articles,
                                          entities=entities, summaries=summaries,
                                          country=c["country"], platform=c["platform"],
                                          lead_cta=not is_leader, own_matches=own_matches)
                     if (c.get("market") or c.get("publisher")) else [])
        # 下载榜新品（ADR 0001 切片 2）：只推 is_slg=True，⬇️ 段单列。
        free_blocks = (build_free_newcomer_lines(c.get("free_market") or {},
                                                 c.get("free_publisher") or {},
                                                 articles=articles, entities=entities,
                                                 own_matches=own_matches)
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
        parts = [f"**{_market_label(c['country'], c['platform'])}**"]
        if mv_blocks:
            parts.append("【榜单异动】\n\n" + "\n\n".join(mv_blocks))
        if nc_blocks:
            parts.append("【新品上架】\n\n" + "\n\n".join(nc_blocks))
        if free_blocks:
            parts.append("【下载榜新品 · SLG】\n\n" + "\n\n".join(free_blocks))
        sections.append("\n\n".join(parts))
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
        vid_lines = build_video_lines(video_items, settings.DIGEST_VIDEO_TOPN)
        if vid_lines:
            total += len(video_items)
            sections.append("【新品实机视频】\n\n" + "\n\n".join(vid_lines))
    # 全局「待建档新厂线索」段（方案①）：下载榜 is_slg=false（白名单未收录）但
    # genre=Strategy 的新品，单列给**维护者**核查建档——补救白名单滞后导致的漏推。
    # **领导卡跳过整段**（建档是维护者动作、对领导是杂讯）；领导卡的「下载榜新品 · SLG」
    # 段仍只推已确认 SLG，不混淆。
    if lead_items and not is_leader:
        lead_lines = build_lead_newcomer_lines(lead_items)
        if lead_lines:
            total += len(lead_lines)
            sections.append(
                "【🔍 待建档新厂线索】（下载榜疑似 SLG、白名单未收录 → 请人工核查建档）"
                "\n\n" + "\n\n".join(lead_lines))
    if not sections:
        return None
    head = f"### 📡 SLG 每日情报 · {today}"
    # 领导卡 TL;DR 不计「待建档 N」（那段已剥离）。对标命中数（⚔️）两卡都计，置顶提威胁面。
    tldr = _digest_tldr(per_combo, version_changes, region_changes, video_items,
                        None if is_leader else lead_items,
                        own_match_count=len(own_matches or {}))
    # 「今日要闻」跨 combo 置顶：全卡最高重要度的事件抽出来放最前，保证核心市场大事件
    # 不被次市场长尾折叠挤掉、领导一眼抓重点（事件数 ≤ TOPN 时不渲染，避免与正文重复）。
    hi_lines = build_highlight_lines(per_combo, settings.DIGEST_HIGHLIGHTS_TOPN, own_matches)
    hi_section = ["【📌 今日要闻】\n\n" + "\n\n".join(hi_lines)] if hi_lines else []
    body = [head] + ([f"> {tldr}"] if tldr else []) + hi_section + sections
    # 按钮按全局重要度排序取头部新品（不再按 combo 地理顺序各取头条挤名额）。
    btns = _ranked_newcomer_buttons(per_combo)
    if overflow:
        # 配了看板基址就把「看板查看全部」做成深链（落到新品页），否则纯文案。
        base = (settings.DASHBOARD_BASE_URL or "").rstrip("/")
        tail = f"[看板查看全部]({base}/newcomers)" if base else "看板查看全部"
        body.append(f"> …另有 **{overflow}** 项未在此展示，{tail}")
    # 链接可达性图例（仅当卡里有 💻 外网链接时挂）：领导手机端据此知道哪些要电脑端。
    if any("💻" in s for s in sections):
        body.append("> 💻 链接需**电脑端**钉钉打开（手机端外网受限）；🎯 看板 · 📰 文章手机可直接点")
    return f"每日情报 {today}", "\n\n---\n\n".join(body), btns


def build_heartbeat_card(today: str) -> tuple[str, str]:
    """平淡日心跳卡（maintainer 卡全空 + 核心 US/iOS 已同步 = 真平淡日）：让收卡方知道
    『系统活着、今日确实平静』，不把静默误读成漏发。仅 DIGEST_HEARTBEAT_ENABLED 开时发。"""
    text = (f"### 📡 SLG 每日情报 · {today}\n\n"
            "> 今日核心市场已同步，SLG 榜单平静——无显著异动 / 新品 / 版本变更。")
    return f"每日情报 {today}", text


def build_data_not_ready_card(today: str) -> tuple[str, str]:
    """数据未就位兜底卡（maintainer 卡全空 + 核心 US/iOS 今日无快照）：同步可能失败 / 配额烧穿，
    主动提醒维护者别把『静默』当『平静』。配套 logger.error→Sentry（见 send_daily_digest）。"""
    text = (f"### ⚠️ SLG 每日情报 · {today} · 数据未就位\n\n"
            "> 核心市场（🇺🇸 美国 · iOS）今日榜单**未同步**，日报暂缺。\n\n"
            "> 可能是 Sensor Tower 配额烧穿或同步任务失败——请查同步日志 / 配额水位。")
    return "每日情报 · 数据未就位", text


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
    # 维护者群或领导群任一配了 webhook 就跑（两群独立，不因没配 maintainer 就漏发领导卡）。
    if not (dingtalk.is_enabled() or dingtalk.leader_target_configured()):
        return False
    from app.services.movement import detect_movement
    from app.services.newcomers import (
        detect_newcomers, detect_publisher_newcomers,
        _load_ignore_keys, _load_entity_matchers, resolve_entity,
        gate_publisher_newcomers_by_release_date,
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
                # 真实上架日门控：剔除老产品（本地"首次出现"≠ 真新品）——与 /publishers
                # 端点同一 helper、同口径，避免 2013–2017 老 SLG 被当新品推给领导。
                publisher["newcomers"] = await gate_publisher_newcomers_by_release_date(
                    publisher.get("newcomers") or [], country, platform)
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
                    f_publisher["newcomers"] = await gate_publisher_newcomers_by_release_date(
                        f_publisher.get("newcomers") or [], country, platform)
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
                    # summary_cn 复用上方 summaries_by_app（lead app ⊆ newcomer_apps，零额外查询）；
                    # #147 已把中文化扩到 is_slg=false 待识别新厂，故这批多数已有中文摘要。
                    lead_items.append({**info, "genre": genre_by_app.get(aid, ""),
                                       "summary_cn": summaries_by_app.get(aid)})
    except Exception:
        logger.exception("Lead newcomer candidates (digest) failed")

    # 「同赛道」：我方产品 → 命中竞品 app_id。**优先按玩法子品类精确匹配**（竞品 subgenre_cn
    # vs 产品 match_subgenre，治题材关键词太宽泛分不出数字门 vs 基地建设）；未配子品类的产品回退
    # 题材关键词（文本=名字 + LLM 中文摘要 summary_cn）。竞品子品类查 market_newcomer_log（前进式：
    # 新品/曾建档竞品有；未分类老竞品=NULL，子品类产品对其不命中=正是要去掉的假阳）。零 ST/LLM。
    own_matches: dict[str, str] = {}
    try:
        own_products = await _load_own_products()
        if own_products:
            # 一次查全部候选竞品（新品三段 + movement）的玩法子品类。
            cand_ids: set[str] = set()
            for c in per_combo:
                for key in ("market", "publisher", "free_market", "free_publisher"):
                    for n in ((c.get(key) or {}).get("newcomers") or []):
                        if n.get("app_id"):
                            cand_ids.add(n["app_id"])
                mv = c.get("movement") or {}
                for k in ("new_entrants", "surges", "drops", "revenue_spikes"):
                    for e in (mv.get(k) or []):
                        if e.get("app_id"):
                            cand_ids.add(e["app_id"])
            subgenre_by_app: dict[str, str] = {}
            if cand_ids:
                from app.models.newcomer import MarketNewcomerLog
                async with AsyncSessionLocal() as db:
                    sgrows = (await db.execute(
                        select(MarketNewcomerLog.app_id, MarketNewcomerLog.subgenre_cn)
                        .where(MarketNewcomerLog.app_id.in_(list(cand_ids)),
                               MarketNewcomerLog.subgenre_cn.is_not(None))
                    )).all()
                for aid, sg in sgrows:
                    subgenre_by_app.setdefault(aid, sg)
            for c in per_combo:
                for key in ("market", "publisher", "free_market", "free_publisher"):
                    for n in ((c.get(key) or {}).get("newcomers") or []):
                        aid = n.get("app_id")
                        if aid and not n.get("is_reentry") and aid not in own_matches:
                            text = " ".join(t for t in (n.get("name"), summaries_by_app.get(aid)) if t)
                            if (m := _match_own_product(text, subgenre_by_app.get(aid), own_products)):
                                own_matches[aid] = m[0]
                mv = c.get("movement") or {}
                for k in ("new_entrants", "surges", "drops", "revenue_spikes"):
                    for e in (mv.get(k) or []):
                        aid = e.get("app_id")
                        if aid and aid not in own_matches:
                            if (m := _match_own_product(e.get("name") or "", subgenre_by_app.get(aid), own_products)):
                                own_matches[aid] = m[0]
    except Exception:
        logger.exception("Own-product match (digest) failed")

    # 同一份检测数据渲染两遍（零额外 ST/查询），分发两个群：
    # - maintainer 卡（全量，含待建档/视频/建档 CTA）→ 维护者群，永远发。
    # - leader 卡（剥离维护者杂讯）→ 领导群，**仅当独立配了 leader webhook** 才发
    #   （未配则只发 maintainer，维持今天的单卡行为，不把领导版卡重发进维护者群）。
    # critical=True：主卡是「每日必达」，终态失败升 Sentry ERROR 让维护者立刻补。
    def _render(audience):
        return build_daily_digest(per_combo, today, articles=articles_by_app,
                                  entities=entities_by_app, version_changes=version_changes,
                                  video_items=video_items, region_changes=region_changes,
                                  summaries=summaries_by_app, lead_items=lead_items,
                                  audience=audience, own_matches=own_matches)

    # 硬锚核心 US/iOS：区分『真平淡日』(已同步、确无事) vs『数据未就位』(今日无快照=同步可能失败)。
    # detect_movement 仅在 today 未缺数据时赋值 entry["movement"]（today_missing 闸门），故它非 None
    # 或 market.as_of==today 即核心 combo 今日有新快照。找不到该 combo（理论不应）→ 保守按已就位、不误报。
    def _core_synced() -> bool:
        for c in per_combo:
            if c["country"] == "US" and c["platform"] == "ios":
                return c.get("movement") is not None or (c.get("market") or {}).get("as_of") == today
        return True

    sent_any = False
    msg_m = _render("maintainer")
    if msg_m is None:
        if not _core_synced():
            # 数据未就位：核心 US/iOS 今日无快照。升 Sentry(ERROR) + 发克制维护者兜底卡，别让
            # 管道故障被『静默=平静』掩盖。leader 卡同源也为空，无需再发。critical=True：兜底卡
            # 自身发失败也升 Sentry。
            logger.error("daily digest: core US/iOS snapshot missing for %s — sync likely failed/incomplete", today)
            return await dingtalk.send_markdown(*build_data_not_ready_card(today),
                                                target="maintainer", critical=True)
        # 真平淡日：默认静默（测试群只有本人、天天收无聊心跳没意义）；DIGEST_HEARTBEAT_ENABLED
        # 开才发心跳卡（推领导群后再开——领导看不到卡会误读『是不是坏了』），两群同发。
        if settings.DIGEST_HEARTBEAT_ENABLED:
            hb = build_heartbeat_card(today)
            sent_any = await dingtalk.send_markdown(*hb, target="maintainer") or sent_any
            if dingtalk.leader_target_configured():
                sent_any = await dingtalk.send_markdown(*hb, target="leader") or sent_any
            return sent_any
        logger.info("daily digest: nothing to report for %s (core synced, quiet day)", today)
        return sent_any
    sent_any = await dingtalk.send_action_card(*msg_m, target="maintainer",
                                               critical=True) or sent_any
    if dingtalk.leader_target_configured():
        msg_l = _render("leader")
        if msg_l is not None:
            sent_any = await dingtalk.send_action_card(*msg_l, target="leader",
                                                       critical=True) or sent_any
    return sent_any


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
    # 维护者运维提醒（含 ssh 重扫码指令）——钉死 maintainer 群，永不进领导群。
    return await dingtalk.send_markdown(*built, target="maintainer")


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
            lines.append(f"🆕 **{_md_name(app.name)}** — {_md_name(entity_name)}（{_platform_tag(app)}）"
                         f"{genre}{released}{_sf_text(app)}")
            if app.track_view_url and len(btns) < 5:
                btns.append((f"{app.name} →", app.track_view_url))   # 按钮 title 纯文本，不过 _md_name
        if len(rows) > 15:
            lines.append(f"…等共 {len(rows)} 款，看板查看全部")
    if expanded:
        lines.append(f"**扩区上线（{len(expanded)} 款，软启动 → 更大范围）**")
        for app, entity_name, added in expanded[:15]:
            added_label = "/".join(s.upper() for s in added)
            now_label = "/".join(s.upper() for s in (app.storefronts or "").split(",") if s)
            lines.append(f"🌍 **{_md_name(app.name)}** — {_md_name(entity_name)} 新增 **{added_label}**（现 {now_label}）")
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
    # 商店雷达是维护者向（厂商建档线索、软启动信号）——钉死 maintainer 群，不进领导群。
    return await dingtalk.send_action_card(title, text, btns, target="maintainer")
