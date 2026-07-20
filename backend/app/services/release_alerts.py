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
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.publisher import PublisherEntity, PublisherItunesApp, PublisherItunesArtist
from app.models.digest import LeaderDigestSend, WechatArticleSent
from app.models.game import CHART_FREE
from app.services import dingtalk, movement

logger = logging.getLogger(__name__)

# 市场 / 平台中文标签（领导面向，去英文）。漏配的国家码回退大写原文。
_COUNTRY_CN = {"us": "美国", "jp": "日本", "kr": "韩国", "cn": "中国", "tw": "台湾",
               "de": "德国", "gb": "英国", "au": "澳洲", "ca": "加拿大", "fr": "法国",
               "ru": "俄罗斯"}
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
    """市场+平台标识（不带榜种），如「美国 · 安卓」。下载榜/跨段复用，避免
    `_combo_label` 的「畅销榜」后缀与下载榜语境打架。

    **不带国旗 emoji**（2026-07-19 用户裁定）：钉钉群消息里国旗是纯装饰，市场中文名本身
    已经说清是哪个市场，去掉后每张卡少十来个 emoji。未收录的国家仍回落大写国码。"""
    cc = _COUNTRY_CN.get(country.lower(), country.upper())
    pf = _PLATFORM_CN.get(platform.lower(), platform)
    return f"{cc} · {pf}"


def _combo_label(country: str, platform: str) -> str:
    return f"{_market_label(country, platform)} 畅销榜"


# 「中文名 西文名」双写主体（建档风格：prod 113 个主体里 31 个长这样）在卡里只显中文段：
# 「壳木游戏 Camel Games」→「壳木游戏」。领导反馈「非中文元素太多看着累」，而西文那半对
# 认厂商没有增量信息。**只认「中文开头 + 空格 + 西文结尾」这一种确定形态**，其余一律原样
# 返回——纯中（莉莉丝）、纯西（FunPlus，本就没通用中文名）、西文在前（StarUnion 星合）、
# 带括号（Sea War (江锋聂) / 新奇互娱 (爱奇艺)）都不动。启发式截断认错主体比多几个西文词更糟。
_CN_THEN_EN = re.compile(r"^([一-鿿][一-鿿0-9·\s]*?)\s+[A-Za-z][A-Za-z0-9.\-&' ]*$")


def _cn_entity(name: Optional[str]) -> str:
    """中英双写主体 → 只留中文段；其余形态原样返回（见 _CN_THEN_EN 注释）。"""
    if not name:
        return name or ""
    m = _CN_THEN_EN.match(name.strip())
    return m.group(1) if m else name


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
        parts.append(f"厂商 {_md_name(_cn_entity(entity))}")
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


# 视频行的行首标记：_video_seg 是它的**唯一**产出处，TL;DR 的 🎬 计数靠数正文里它的
# 出现次数（见 build_daily_digest）。改文案请连带这里，别让计数与正文再次脱钩。
_VIDEO_SEG_PREFIX = "🎬 实机视频"


def _video_seg(videos: Optional[dict], app_id: Optional[str]) -> str:
    """新品行内联的实机视频段。取代此前独立的【新品实机视频】段——那段重列同一批新品名
    （视频项全来自当日新品），领导反馈「同样的游戏说了两遍」。改为并进各新品行的动作行。
    videos: {app_id: {count, url}}。该 app 无视频 → 返回 ""。"""
    v = (videos or {}).get(app_id or "")
    if not v:
        return ""
    link = f" 💻 [看第一条]({v['url']})" if v.get("url") else ""   # YouTube=外网，手机打不开
    return f"{_VIDEO_SEG_PREFIX} {v['count']} 条{link}"


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
# movement 五类 → (kind, summary 字段名)，多处复用（行渲染排序 / 今日要闻收集 / 平淡日计数）。
_MOVEMENT_KINDS = (("new_entrant", "new_entrants"), ("surge", "surges"),
                   ("drop", "drops"), ("revenue_spike", "revenue_spikes"),
                   ("climb", "climbs"))
# 只要 summary 字段名的场景（遍历 movement 各类事件）用这个，别再手写元组：**从
# _MOVEMENT_KINDS 派生**，新增/改名事件类型时两者不可能不同步。散落硬编码正是「某处
# 少写一类」的温床——同族的 _NEWCOMER_SOURCE_KEYS 就有「曾漏 free 两层」的前科，
# 2026-07-19 又连着漏了三次（领导卡过滤 / 实体解析 / TL;DR 计数）。
_MOVEMENT_KEYS = tuple(key for _, key in _MOVEMENT_KINDS)
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
    if kind == "climb":
        # 连涨比单日 surge 温和（渐进 ≠ 突发），基分略低；且多止步中段，_rank_height 天然偏低，
        # 不会盖过头部空降/高名次收入异动，也不刷屏今日要闻。
        drop = max(0, (e.get("start_rank") or 0) - (e.get("cur_rank") or 0))
        return 2.5 + 4 * _rank_height(e.get("cur_rank")) + min(drop, 40) * 0.12
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
    「看板」的 🎯。产品名过 _md_name 防破版。

    maxlen 用 _md_name 默认值（32）、**不再收窄到 20**（2026-07-19 领导卡实证）：原先
    自家产品名比同一行的竞品名截得更狠，5 款产品里 2 款被砍——`Blade War:Three Kingdoms`
    (24) 显示成「Blade War:Three Kin…」。这个标签的**全部价值就是告诉领导「我方哪款被
    打」**，名字截了就等于没说；何况自家产品名来自内部 own_products 表（可控小集合），
    没有比 ST 原始竞品名更需要防破版的理由。"""
    name = (own_matches or {}).get(app_id)
    return f" ⚔️《{_md_name(name)}》同赛道" if name else ""


def _sg_label(entry: dict) -> str:
    """新品/异动行的中文玩法子品类标签 ` · 数字门SLG`——让外文游戏名一眼可辨品类
    （韩/日/英文名读者看不懂「是什么游戏」时，子品类补足语义）。无则空。
    数据由 send_daily_digest 用 _subgenres_for_apps 富化进 entry['subgenre_cn']。"""
    sg = (entry.get("subgenre_cn") or "").strip()
    return f" · {sg}" if sg else ""


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


async def _subgenres_for_apps(app_ids: set[str]) -> dict[str, str]:
    """候选竞品 app_id → 玩法子品类 `subgenre_cn`（给 own_matches「同赛道」匹配用）。

    `market_newcomer_log` 优先（新品/曾建档竞品有），缺的 fallback `app_subgenre`（P1-2 存量
    回补，覆盖 movement 老熟人 + subgenre 特性前老检出行）——让 ⚔️ 同赛道对老竞品也生效。零 ST。
    """
    out: dict[str, str] = {}
    if not app_ids:
        return out
    from app.models.newcomer import MarketNewcomerLog, AppSubgenre
    ids = list(app_ids)
    async with AsyncSessionLocal() as db:
        for aid, sg in (await db.execute(
            select(MarketNewcomerLog.app_id, MarketNewcomerLog.subgenre_cn)
            .where(MarketNewcomerLog.app_id.in_(ids),
                   MarketNewcomerLog.subgenre_cn.is_not(None))
        )).all():
            out.setdefault(aid, sg)
        missing = [a for a in ids if a not in out]
        if missing:
            for aid, sg in (await db.execute(
                select(AppSubgenre.app_id, AppSubgenre.subgenre_cn)
                .where(AppSubgenre.app_id.in_(missing),
                       AppSubgenre.subgenre_cn.is_not(None))
            )).all():
                out.setdefault(aid, sg)
    return out


async def _slg_gate_probe_items(items: list[dict],
                                id_key: str = "app_id") -> tuple[list[dict], list[dict]]:
    """探测层（商店雷达 / RSS 早鸟）产品级 SLG 门控：LLM 玩法子品类 ∈ SLG 核心口径才推。

    厂商级 is_slg 挡不住这类噪声（Plarium 是真 SLG 大厂、新品 LegendUP 却是放置 RPG；
    2026-07-16 平淡日领导卡实证）。分类信号免费——影子行富化管道早就在给这些 app 跑
    subgenre 分类，此处只是渲染前消费它。

    三态处理：分类命中 SLG 子集 → 推；分类为非 SLG（含 app_subgenre 已试非词表=NULL）
    → 滤；**尚未分类 / 无 app_id → 也滤**（宁缺勿噪——分类通常当日 drain 补上，雷达
    2 天窗口 / RSS 台账不重报以内自然赶上；预注册页无描述分类不出的，看板/DB 仍可溯）。
    返回 (保留项, 滤除明细 [{name, subgenre}])；滤除明细由维护者卡渲染成折叠行——条数
    少时**带名字+分类**（治「LLM 误判真 SLG → 静默永不推」盲区，人眼可抓误杀），多时
    只计数。不静默丢（no silent caps）。
    """
    from app.services.newcomer_i18n import SLG_CORE_SUBGENRES
    if not items:
        return [], []
    ids = {it.get(id_key) for it in items if it.get(id_key)}
    sg = await _subgenres_for_apps(ids)
    kept, cut = [], []
    for it in items:
        aid = it.get(id_key)
        if aid and sg.get(aid) in SLG_CORE_SUBGENRES:
            kept.append(it)
        else:
            cut.append({"name": it.get("name") or aid or "?",
                        "subgenre": sg.get(aid) if aid else None})
    return kept, cut


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
    for e in s.get("climbs", []):
        # ↗️ 连涨（区别 📈 单日窜升）：多日稳步爬升，标注跨度天数让领导看清「持续动量」而非一日抖动。
        scored.append((_event_score("climb", e),
                       f"↗️ **{_md_name(e['name'])}** 连涨 #{e['start_rank']} → **#{e['cur_rank']}**（{e['span_days']}天累计 ↑{e['start_rank'] - e['cur_rank']}）" + _tag(e) + _meta(e)))
    for e in s["drops"]:
        to = "榜外" if e["cur_rank"] is None else f"#{e['cur_rank']}"
        phrase = movement.drop_phrase(e["prev_rank"], e["cur_rank"])
        scored.append((_event_score("drop", e),
                       f"📉 **{_md_name(e['name'])}** {phrase}（#{e['prev_rank']} → {to}）" + _tag(e) + _meta(e)))
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


# 新品的四层来源：收入榜（市场/厂商）+ 下载榜（市场/厂商）。搜关键词与文章回挂都要
# 覆盖全四层——否则下载榜新品「搜了却挂不上」（曾漏 free 两层，下载榜 SLG 新品永远无文章）。
_NEWCOMER_SOURCE_KEYS = ("market", "publisher", "free_market", "free_publisher")


def _newcomer_search_keywords(per_combo: list[dict], max_n: int) -> list[str]:
    """从当日新品（收入榜 + 下载榜两层）挑搜文章关键词，按优先级排序后截断。

    治三个坑：① set 截断非确定（`list(set)[:N]` 受 hash 随机化，每次跑搜的不是同一批）；
    ② 无优先级（核心 SLG/头部名次该先搜）；③ reentry 占额（回归老游戏挤掉真首发）。
    优先级：SLG（is_slg 或已归属主体）> 非回归 > 名次靠前；同级按 per_combo 出现序稳定。
    reentry 仍收但排末位、配额紧时先被截掉。返回去重后的前 max_n 个**游戏名**。"""
    best: dict[str, tuple] = {}
    for c in per_combo:
        for key in _NEWCOMER_SOURCE_KEYS:
            for n in ((c.get(key) or {}).get("newcomers") or []):
                nm = n.get("name")
                if not nm:
                    continue
                pk = (0 if (n.get("is_slg") or n.get("entity_id")) else 1,
                      1 if n.get("is_reentry") else 0,
                      n.get("rank") or 9999)
                if nm not in best or pk < best[nm]:
                    best[nm] = pk
    return [nm for nm, _ in sorted(best.items(), key=lambda kv: kv[1])][:max_n]


def _match_articles_to_apps(per_combo: list[dict], article_list: list,
                            extra_rows: Optional[list[dict]] = None) -> dict:
    """搜到的文章 → 按「标题/摘要含新品名」聚合到 app_id：{app_id: [WechatArticle]}。

    用 (c.get("market") or {}) 而非 c.get("market", {})——entry 的 market/publisher
    初始为 None，后者在 key 存在时返回 None 会 AttributeError（曾导致整段静默失效）。

    覆盖**全四层**新品来源（收入榜 + 下载榜市场/厂商）——下载榜新品名同样进了搜索关键词，
    回挂也必须含 free 两层，否则【下载榜新品】行永远拿不到 📰（F1：搜了却挂不上）。

    extra_rows（ADR 0006 切片2）：榜单四层之外的补充名单（{name, app_id} dict 列表，现用
    商店雷达近期新上架）——雷达新品名进了搜索关键词，回挂也要含它们，否则同 F1 坑。

    名 ↔ 文匹配走 _name_matches（词边界 / 最小名长），治裸 substring 的短名/通用名
    误挂 + 拉丁名大小写漏挂。
    """
    name_to_apps: dict[str, list[str]] = {}
    for c in per_combo:
        rows = []
        for key in _NEWCOMER_SOURCE_KEYS:
            rows += (c.get(key) or {}).get("newcomers") or []
        for n in rows:
            nm, aid = n.get("name"), n.get("app_id")
            if nm and aid:
                apps = name_to_apps.setdefault(nm, [])
                if aid not in apps:
                    apps.append(aid)
    for n in (extra_rows or []):
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
                         own_matches: Optional[dict] = None,
                         videos: Optional[dict] = None) -> list[str]:
    """两层新品检测 → 人读行。
    enrich: {app_id: {genre, price, release_date}}
    videos: {app_id: {count, url}} —— 该新品已搜集的实机视频，内联进动作行（取代独立段）
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
    shown_market_ids: set = set()   # 已渲染的市场行 app_id——publisher 层按此去重
    for n in market_real[:10]:
        aid = n.get("app_id")
        is_lead_row = not n.get("is_slg")
        if is_lead_row:
            if lead_shown >= settings.DIGEST_MARKET_LEAD_TOPN:
                continue   # 超额待识别新厂：不逐条列，循环后统一折叠（见下方 lead_hidden）
            lead_shown += 1
        shown_market_ids.add(aid)
        # #99 忽略名单过滤后，is_slg=false 多是「真新厂商线索」而非噪声——维护者卡升级文案带
        # 行动指引（建议建档）+ 行内商店页直达；领导卡 lead_cta=False 剥掉这套维护者动作。
        is_lead = is_lead_row and lead_cta
        tag = "  ⚠️ 新厂商待识别 · 建议建档" if is_lead else ""
        en = enrich.get(aid) or {}
        inner = _meta_inner(genre=en.get("genre"), revenue=n.get("revenue"),
                            downloads=n.get("downloads"),
                            entity=entities.get(aid) or n.get("publisher"))
        action = " · ".join(s for s in (
            _link_line(aid or "", "market", country=country, platform=platform,
                       with_store=is_lead, articles=articles.get(aid)),
            _video_seg(videos, aid)) if s)
        lines.append(_block([
            f"✨ **{_md_name(n['name'])}** 空降 **#{n['rank']}**{_sg_label(n)}{tag}{_own_tag(aid, own_matches)}",
            f"> {inner}" if inner else "",
            f"📝 {summaries.get(aid)}" if summaries.get(aid) else "",   # LLM 一句话：领导秒懂
            action,
        ]))
    lead_hidden = lead_total - lead_shown
    if lead_hidden > 0:
        base = (settings.DASHBOARD_BASE_URL or "").rstrip("/")
        tail = f"，[看板核查]({base}/newcomers)" if base else ""   # 看板深链手机可达，不标 💻
        lines.append(f"> …另有 **{lead_hidden}** 个未识别新面孔上榜{tail}")
    # 同 combo 两层按 app_id 互斥（对齐 free 层 merged 写法）：已建档主体的新品进了
    # 市场层 Top50 时，✨ 市场行与 🏢 厂商行会把同一游戏渲染两遍——市场行先到先得
    # （厂商归属已在其 meta 行内），主体层只补市场层没露出的深名次行。
    publisher_real = [n for n in (publisher.get("newcomers") or [])
                      if not n.get("is_reentry") and n.get("app_id") not in shown_market_ids]
    for n in publisher_real[:10]:
        aid = n.get("app_id")
        rank = f"#{n['rank']}" if n.get("rank") else "进榜"
        inner = _meta_inner(revenue=n.get("revenue"), downloads=n.get("downloads"))
        action = " · ".join(s for s in (
            _link_line(aid or "", "publisher", articles=articles.get(aid)),
            _video_seg(videos, aid)) if s)
        lines.append(_block([
            f"🏢 **{_md_name(_cn_entity(n['entity_name']))}** 新品 **{_md_name(n['name'])}** {rank}{_sg_label(n)}{_own_tag(aid, own_matches)}",
            f"> {inner}" if inner else "",
            f"📝 {summaries.get(aid)}" if summaries.get(aid) else "",
            action,
        ]))
    return lines


def build_free_newcomer_lines(market: dict, publisher: dict,
                              articles: Optional[dict] = None,
                              entities: Optional[dict] = None,
                              own_matches: Optional[dict] = None,
                              videos: Optional[dict] = None,
                              summaries: Optional[dict] = None) -> list[str]:
    """下载榜新品 → 人读行（ADR 0001 切片 2）。

    **钉钉只推 is_slg=True**（下载榜噪声大：休闲/工具类装机榜混入多）——非 SLG 的
    下载榜新品仍照常入库 + 看板可见，只是不进钉钉卡片（口径差异是刻意的，见 ADR）。
    回归同样过滤。⬇️ 前缀与收入榜区分。市场+主体两路按 app_id 去重。
    own_matches: {app_id: 我方产品名} —— 命中则行尾打「⚔️《X》同赛道」。
    """
    from app.services.slg_publishers import is_slg
    articles = articles or {}
    entities = entities or {}
    summaries = summaries or {}
    merged: dict[str, dict] = {}
    for n in (market.get("newcomers") or []):
        if n.get("is_slg") and not n.get("is_reentry"):
            merged[n["app_id"]] = n
    for n in (publisher.get("newcomers") or []):
        aid = n.get("app_id")
        if n.get("is_reentry") or aid in merged:
            continue
        # 主体行也按 is_slg 门控（必须 SLG 才推）。行级 is_slg 先查——digest 拼卡前
        # 已做跨 combo OR 传播回写，比 live 判定多认得「别的 combo 判过 SLG」的行。
        if n.get("is_slg") or is_slg(aid, n.get("publisher")):
            merged[aid] = n
    lines = []
    for n in list(merged.values())[:10]:
        aid = n.get("app_id")
        rank = f"#{n['rank']}" if n.get("rank") else "上榜"
        inner = _meta_inner(downloads=n.get("downloads"),
                            entity=n.get("entity_name") or entities.get(aid) or n.get("publisher"))
        action = " · ".join(s for s in (
            _link_line(aid or "", "market", articles=articles.get(aid)),
            _video_seg(videos, aid)) if s)
        lines.append(_block([
            f"⬇️ **{_md_name(n['name'])}** 下载榜 **{rank}**{_sg_label(n)}{_own_tag(aid, own_matches)}",
            f"> {inner}" if inner else "",
            # 下载榜是「收入未起、下载先爆」的最早期信号，领导最缺先验——📝 与收入榜行对齐
            f"📝 {summaries.get(aid)}" if summaries.get(aid) else "",
            action,
        ]))
    return lines


def collect_lead_candidates(per_combo: list[dict]) -> dict[str, dict]:
    """从 free 榜新品收集「待建档新厂线索」候选（方案①），按 app_id 去重留首见。

    入选条件：is_slg=false（白名单未收录）+ 非回归 + **未归属任何已建档主体**。
    最后一条是关键：`detect_publisher_newcomers` 产出的 free_publisher 行**已归属**
    （带 `entity_id`），按定义绝不是「待建档新厂线索」——但其 `_row_dict` 不含 is_slg
    字段，`not n.get("is_slg")` 对它恒为真，故必须再用 `not n.get("entity_id")` 排掉，
    否则已建档主体的新品（如 Camel Games 的 Frontier City、Larks Holding 的 Last Siren）
    会同时出现在「厂商新品」段和「待建档」段（症状：digest 把已归属产品当新厂线索推）。
    genre 初筛由调用方按 free 行 genre 另行压休闲噪声。纯函数、零查询。"""
    cand: dict[str, dict] = {}
    for c in per_combo:
        for key in ("free_market", "free_publisher"):
            for n in ((c.get(key) or {}).get("newcomers") or []):
                aid = n.get("app_id")
                if (aid and not n.get("is_slg") and not n.get("is_reentry")
                        and not n.get("entity_id") and aid not in cand):
                    cand[aid] = {"app_id": aid, "name": n.get("name"),
                                 "publisher": n.get("publisher"), "rank": n.get("rank"),
                                 "country": c["country"], "platform": c["platform"]}
    return cand


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
    """版本变更 → 人读行（需求② / ADR 0003）。changes: [{name, old, new, date, notes_cn?}]。

    全局段（跨 combo），tracked iOS 竞品版本更新。封顶 cap 防极端日刷屏。
    notes_cn（版本更新说明的 LLM 一句话实质摘要，见 version_tracker._summarize_notes）
    有值时补一条 📝 子行——把「1.1.8 → 1.1.9」这种纯版本号变成「新赛季 X / 平衡调整」可读情报；
    纯 bugfix / 无 notes 时 notes_cn=None，只显版本号不加噪。
    """
    out: list[str] = []
    for c in changes[:cap]:
        date = f"（{c['date']}）" if c.get("date") else ""
        line = f"🆙 **{_md_name(c['name'])}**：{_md_name(c['old'], maxlen=None)} → {_md_name(c['new'], maxlen=None)}{date}"
        notes_cn = (c.get("notes_cn") or "").strip()
        if notes_cn:
            line += f"\n   📝 {_md_name(notes_cn, maxlen=60)}"
        out.append(line)
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


def _primary_item_count(per_combo: list[dict], version_changes, region_changes) -> int:
    """当日『竞品实质信号』计数：异动 + 四层新品 + 版本 + 新区（不含待建档/兜底填充段）。
    用于判『平淡日』→ 触发兜底填充（行业动态段 + 领导卡雷达段）。

    口径 = **真正会上卡的**，与渲染层对齐：
    - market 层只数 is_slg——`is_slg=false` 是「待识别新厂」建档线索（本 docstring 声明
      「不含待建档」，`build_daily_digest` 的领导卡也在一处剥离它们）；
    - 排除 is_reentry（回归≠首发，渲染层 market_real/publisher_real 同口径已滤）。

    2026-07-15 RU 同步日实证这条为何必要：次市场双周同步一次涌进大量新面孔，market 层
    4 条全 is_slg=0（足球/塔防/经营等噪声）+ 3 条异动 = 7 ≥ 阈值 6 → is_quiet=False；
    但领导卡剥离那 4 条后只剩 3 条异动的空卡，行业动态与雷达段双双没出——即「最需要兜底
    的次市场同步日，恰恰因为噪声撑高计数而不兜底」。
    """
    n = 0
    for c in per_combo:
        mv = c.get("movement") or {}
        for k in _MOVEMENT_KEYS:
            n += len(mv.get(k) or [])
        for key in _NEWCOMER_SOURCE_KEYS:
            for x in ((c.get(key) or {}).get("newcomers") or []):
                if x.get("is_reentry"):
                    continue
                if key == "market" and not x.get("is_slg"):
                    continue
                n += 1
    return n + len(version_changes or []) + len(region_changes or [])


def build_industry_lines(articles: list, cap: int) -> list[str]:
    """平淡日「SLG 行业动态」段：公众号广搜的近期行业/新品文章 → 链接行。**非我方追踪
    竞品**的行业面背景（区别于按新品名精确回挂的 📰），故独立段 + 明确标注、仅维护者卡。"""
    out: list[str] = []
    for a in articles[:cap]:
        link = getattr(a, "link", "") or ""
        title = " ".join(((getattr(a, "title", "") or "")
                          .replace("[", "(").replace("]", ")").replace("|", "/")).split())
        if not link or not title:
            continue
        author = getattr(a, "author", "") or ""
        src = f" · {_md_name(author)}" if author else ""
        out.append(f"📰 [{title}]({link}){src}")
    return out


async def _recent_radar_arrivals(days: int, cap: int = 8) -> list[dict]:
    """商店雷达近 days 天的非基线新上架（publisher_itunes_apps，零 ST）→ 紧凑 dict 列表。
    平淡日维护者卡兜底段用；按 first_seen_at 倒序、封顶 cap。_platform_tag/_sf_text/
    _radar_store_country 在下方「应用商店雷达」节定义（模块级，call-time 解析，前引无碍）。"""
    from datetime import timedelta
    from app.models.newcomer import MarketNewcomerLog, NewcomerVideo
    cutoff = utcnow_naive() - timedelta(days=days)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(PublisherItunesApp, PublisherEntity.name)
            .join(PublisherEntity, PublisherEntity.id == PublisherItunesApp.entity_id)
            .where(PublisherItunesApp.is_baseline.is_(False),
                   PublisherItunesApp.first_seen_at >= cutoff)
            .order_by(PublisherItunesApp.first_seen_at.desc())
            .limit(cap)
        )).all()
        # P1-1：这些雷达新上架若已写影子行并被中文化 / 搜到实机视频，带上 📝 摘要 + 🎬 视频
        # （track_id ≡ 影子行 app_id ≡ NewcomerVideo.app_id）。
        track_ids = [app.track_id for app, _ in rows if app.track_id]
        summaries: dict[str, str] = {}
        videos: dict[str, dict] = {}
        if track_ids:
            for aid, sc in (await db.execute(
                select(MarketNewcomerLog.app_id, MarketNewcomerLog.summary_cn).where(
                    MarketNewcomerLog.app_id.in_(track_ids),
                    MarketNewcomerLog.summary_cn.is_not(None))
            )).all():
                summaries.setdefault(aid, sc)
            for v in (await db.execute(
                select(NewcomerVideo)
                .where(NewcomerVideo.app_id.in_(track_ids),
                       NewcomerVideo.hidden_at.is_(None))
                .order_by(NewcomerVideo.app_id, NewcomerVideo.rank.is_(None),
                          NewcomerVideo.rank, NewcomerVideo.id)
            )).scalars().all():
                slot = videos.setdefault(v.app_id, {"count": 0, "url": None})
                slot["count"] += 1
                if slot["url"] is None and v.url:
                    slot["url"] = v.url
    return [{"name": app.name, "entity": entity_name, "platform_tag": _platform_tag(app),
             "genre": app.genre or "", "sf": _sf_text(app),
             "app_id": app.track_id,
             "platform": "android" if (app.storefronts or "") == "gp" else "ios",
             "country": _radar_store_country(app),
             "summary": summaries.get(app.track_id),
             "video": videos.get(app.track_id)} for app, entity_name in rows]


def build_radar_recent_lines(items: list[dict], cap: int,
                             articles: Optional[dict] = None) -> list[str]:
    """商店雷达近期新上架 → 紧凑行（维护者卡有则即显；领导卡仅平淡日兜底，ADR 0006 切片2）。
    是厂商开发者账号清单 diff 的 catch，非我方 tracked 竞品排名动态，故独立段 + 明确标注。
    articles: {app_id: [WechatArticle]}——雷达新品名也进了公众号搜索面，有文即挂 📰。"""
    videos = {it["app_id"]: it["video"]
              for it in items if it.get("app_id") and it.get("video")}
    out: list[str] = []
    for it in items[:cap]:
        genre = f" · {it['genre']}" if it.get("genre") else ""
        line = (f"🛒 **{_md_name(it['name'])}** — {_md_name(it['entity'])}"
                f"（{it['platform_tag']}）{genre}{it.get('sf') or ''}")
        parts = [line]
        # P1-1：软启动新品已中文化则补一句话 📝 摘要（雷达段此前只有裸名+区）。
        if it.get("summary"):
            parts.append(f"📝 {_md_name(it['summary'], maxlen=60)}")
        # 商店页直达（iOS 数字 id → App Store / GP 包名 → Google Play）+ 实机视频动作行，
        # 与新品行同款；雷达影子行不进看板新品网格，故详情页入口只能是真商店链接。
        # 💻 = 外网、手机端受限，走底部图例。
        aid = it.get("app_id") or ""
        segs = []
        if (url := _store_url(aid, it.get("country") or "us", it.get("platform") or "")):
            segs.append(f"💻 [商店页]({url})")
        if (vseg := _video_seg(videos, aid)):
            segs.append(vseg)
        if segs:
            parts.append(" · ".join(segs))
        # ADR 0006 切片2：📰 文章（复用新品行的 sanitize/两篇封顶；strip 掉其行内缩进
        # 前缀——雷达行按 _block 空行分段，钉钉 markdown 单 \n 会粘连）。
        if articles and (sfx := _articles_suffix(articles.get(aid))):
            parts.append(sfx.strip())
        out.append(_block(parts))
    return out


def _digest_tldr(per_combo: list[dict], version_changes, region_changes,
                 video_count: int, lead_items, own_match_count: int = 0) -> str:
    """开头一句话总览（TL;DR）：让领导打开卡片先有「今天整体什么情况」的锚点，不用读完
    全卡才判断。新品按 app_id 去重跨榜/combo（市场+厂商+下载榜同一 app 只算一次）。
    own_match_count：命中「对标我方哪款」的竞品数——正向锚点，放最前让领导先看威胁面。
    video_count：**正文实际渲染出的**视频行数（非 video_items 全量），由调用方数出来传入
    ——视频只内联在新品/雷达行上，受众剥离与封顶都会让部分行不出现（见调用处注释）。"""
    move = 0
    new_apps: set = set()
    for c in per_combo:
        mv = c.get("movement") or {}
        move += sum(len(mv.get(k) or []) for k in
                    _MOVEMENT_KEYS)
        for key in _NEWCOMER_SOURCE_KEYS:
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
    if video_count:
        bits.append(f"🎬 视频 {video_count}")
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


def build_highlight_index(per_combo: list[dict], topn: int,
                          own_matches: Optional[dict] = None) -> str:
    """「今日重点」一行索引：按重要度指出**该先看哪个市场**，但不复述具体事件。

    取代原先逐条列 Top N 事件的【📌 今日要闻】段。那段把正文里已有的事件原样再讲一遍，
    已有的两道去重（排除正文首位 combo、事件数 ≤ TOPN 不渲染）都挡不住：2026-07-19 领导卡
    实测 5 条要闻在正文中**全部重复**——首位 combo 是 US、要闻上浮的全是 JP 的，而 JP 段
    正文完整渲染。改一行索引后与 TL;DR 正交（那边按**事件类型**汇总，这边按**市场**汇总），
    零内容重复，仍保留「一眼知道今天该往哪看」的导航价值。

    **按实际有事件的市场数**判断是否渲染：只剩一个市场时这行是废话（正文就那一段，无从
    导航），空 combo 也不该把它凑成「多市场」。某市场命中「同赛道」则打 ⚔️ 提威胁面。
    """
    if topn <= 0:
        return ""
    agg: dict[tuple, dict] = {}
    for score, it in _collect_scored_items(per_combo, own_matches):
        key = (it["country"], it["platform"])
        a = agg.setdefault(key, {"n": 0, "top": 0.0, "own": False})
        a["n"] += 1
        a["top"] = max(a["top"], score)
        if own_matches and it["e"].get("app_id") in own_matches:
            a["own"] = True
    if len(agg) <= 1:
        return ""
    ordered = sorted(agg.items(), key=lambda kv: kv[1]["top"], reverse=True)[:topn]
    parts = [f"{_market_label(c, p)} {a['n']} 项{' ⚔️' if a['own'] else ''}"
             for (c, p), a in ordered]
    # 市场之间用 ｜ 而非 ·：市场标签内部本就含 ·（「日本 · iOS」），同符号套同符号会糊掉层次。
    return "📌 重点：" + " ｜ ".join(parts)


def _combo_sort_key(c: dict) -> tuple[float, float]:
    """combo 排序键（降序）：市场权重为主、combo 内最高单项强度为辅。让核心市场（US/iOS）
    稳居前列、永不被次市场长尾的全局封顶挤掉；同权重市场里有大事件的 combo 上浮。"""
    mw = _market_weight(c.get("country", ""), c.get("platform", ""))
    mv = c.get("movement") or {}
    best = 0.0
    for kind, key in _MOVEMENT_KINDS:
        for e in mv.get(key) or []:
            best = max(best, _event_score(kind, e))
    for key in _NEWCOMER_SOURCE_KEYS:
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


def build_rss_earlybird_lines(items: list[dict], cap: int) -> list[str]:
    """「⚡ RSS 早鸟」段（ADR 0005）：次市场当日 RSS 策略畅销榜的新面孔——ST 快照
    还没到（双周一拍），这是最早的免费信号。已识别 SLG 优先、按名次升序；仅维护者卡
    （早鸟未过 ST 口径核实，对领导是噪声风险；确认后自然经 ST 检出进正常通道）。"""
    out: list[str] = []
    ordered = sorted(items, key=lambda x: (not x.get("is_slg"), x.get("rank") or 999))
    for it in ordered[:cap]:
        tag = "" if it.get("is_slg") else "  ⚠️ 待识别"
        pub = f" · {_md_name(it['publisher'])}" if it.get("publisher") else ""
        out.append(f"⚡ **{_md_name(it['name'])}** "
                   f"{_market_label(it['country'], 'ios')} 策略畅销 **#{it.get('rank')}**"
                   f"{pub}{tag}")
    return out


def build_daily_digest(per_combo: list[dict], today: str,
                       articles: Optional[dict] = None,
                       entities: Optional[dict] = None,
                       version_changes: Optional[list[dict]] = None,
                       video_items: Optional[list[dict]] = None,
                       region_changes: Optional[list[dict]] = None,
                       summaries: Optional[dict] = None,
                       lead_items: Optional[list[dict]] = None,
                       audience: str = "maintainer",
                       own_matches: Optional[dict] = None,
                       industry_articles: Optional[list] = None,
                       radar_items: Optional[list] = None,
                       rss_items: Optional[list[dict]] = None,
                       quiet_day: bool = False,
                       probe_filtered: Optional[list[dict]] = None) -> Optional[tuple[str, str, list[tuple[str, str]]]]:
    """全 combo 检测结果 → (title, markdown, btns)。全空 → None（不发）。

    per_combo: [{country, platform, movement: dict|None, market: dict|None, publisher: dict|None}]
    articles: {app_id: [WechatArticle]} 微信公众号文章（按 app_id 聚合）
    entities: {app_id: 中文厂商主体}（市场新面孔 / 异动行补中文归属）
    own_matches: {app_id: 我方产品名}（对标我方哪款，命中给该竞品行打「⚔️《X》同赛道」+ TL;DR 计数）
    audience: "maintainer"（默认，全量卡）/ "leader"（领导卡）。领导卡剥离维护者杂讯：
    跳过「待建档新厂线索」整段、新品行不拼「建议建档」尾标、TL;DR 不计「待建档 N」、
    只看 SLG 产品（market 层 is_slg=false 待识别新厂剥离）。**【榜单异动】两卡都含**
    （2026-06-30 应要求加回，撤 #164 的剥离——movement 本就只含已识别 SLG 老熟人的排名
    进退，对领导是有效竞品动态；正文段 / 今日要闻 / TL;DR 异动计数随之三处统一回来）。
    检测数据两个 audience 共用一份（send_daily_digest 渲染两遍），零额外 ST。
    """
    is_leader = audience == "leader"
    # 领导卡口径「只看 SLG 产品」：剥离 market / free 两层的「待识别新厂」(is_slg=false——
    # 含足球/塔防/恐怖等非 SLG 噪声 + 白名单未收录的真新厂线索)。已识别 SLG 厂的 publisher
    # 新品保留。在此**一处**过滤，正文/TL;DR/今日要闻/按钮所有出口统一从过滤后 per_combo
    # 取，避免逐出口判漏。维护者卡(全量 + 建档线索)不过滤。
    # 不 mutate 入参（浅拷副本：换掉 combo 的对应 newcomers，原入参不动）。
    # **free 层曾漏过**（2026-07-19 修）：原注释以为「free 已 is_slg 门控」就不用在这过滤，
    # 但那道门控在**渲染层**（build_free_newcomer_lines），不走渲染层的 TL;DR 直接读
    # per_combo，于是把下载榜非 SLG 新品也计进领导卡「✨ 新品 N」——正文一条都不显示。
    # 实测当日两卡新品数完全相同(均 7)即是它没生效的实证。
    # 注：【榜单异动】两卡都含——2026-06-30 应要求加回（撤 #164 的 movement=None 剥离）。
    # movement 只含已识别 SLG 老熟人的排名进退，是有效竞品动态，不再对领导卡置 None。
    if is_leader:
        from app.services.slg_publishers import is_slg as _is_slg

        def _leader_keeps(key: str, n: dict) -> bool:
            """与**正文渲染**同口径（build_free_newcomer_lines）：free_publisher 行级
            is_slg 优先、回退 live 厂商判定；其余看行级 is_slg。口径一分叉，TL;DR 就又会
            报出正文没有的东西。"""
            if key == "free_publisher":
                return bool(n.get("is_slg")) or _is_slg(n.get("app_id"), n.get("publisher"))
            return bool(n.get("is_slg"))

        _filtered = []
        for _c in per_combo:
            _new = dict(_c)
            for _key in ("market", "free_market", "free_publisher"):
                _seg = _c.get(_key)
                if _seg and _seg.get("newcomers"):
                    _new[_key] = {**_seg,
                                  "newcomers": [n for n in _seg["newcomers"]
                                                if _leader_keeps(_key, n)]}
            _filtered.append(_new)
        per_combo = _filtered
    sections: list[str] = []
    cap = settings.DIGEST_MAX_ITEMS
    mv_cap = settings.DIGEST_MOVEMENT_TOPN
    # 实机视频按 app_id 内联进各新品行（取代独立段，免同产品名列两遍）。
    videos_by_app = {v["app_id"]: v for v in (video_items or []) if v.get("app_id")}
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
                                          lead_cta=not is_leader, own_matches=own_matches,
                                          videos=videos_by_app)
                     if (c.get("market") or c.get("publisher")) else [])
        # 下载榜新品（ADR 0001 切片 2）：只推 is_slg=True，⬇️ 段单列。
        free_blocks = (build_free_newcomer_lines(c.get("free_market") or {},
                                                 c.get("free_publisher") or {},
                                                 articles=articles, entities=entities,
                                                 own_matches=own_matches, videos=videos_by_app,
                                                 summaries=summaries)
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
    # 「新品实机视频」不再单列整段——已内联进各新品行的动作行（🎬，见 build_newcomer_lines），
    # 免同一批新品名在【新品上架】和【实机视频】列两遍（领导反馈的重复）。TL;DR 仍计 🎬 N，
    # 但只计**正文真渲染出来**的那些（见下方 videos_shown）——否则领导卡会报出它看不到的视频。
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
    # RSS 早鸟段（ADR 0005）：次市场当日 RSS 策略畅销榜的新面孔，ST 快照未到时的
    # 最早免费信号。仅维护者卡——未过 ST 口径核实，领导卡不加（减量宪法）；ST 双周
    # 快照到位后同一 app 会经正常检出通道进两卡（届时 RSS 影子行已让它带上翻译/视频）。
    if rss_items and not is_leader:
        rss_lines = build_rss_earlybird_lines(rss_items, cap)
        if rss_lines:
            total += len(rss_lines)
            sections.append(
                "【⚡ RSS 早鸟】（次市场当日榜 · ST 快照未到 · Apple RSS 免费源）"
                "\n\n" + "\n\n".join(rss_lines))
    # 商店雷达近期新上架（ADR 0006 切片2）：厂商开发者账号清单 diff 的 catch = 上榜前
    # 早探测层。**维护者卡有则即显**（2026-07-09 起，原仅平淡日兜底——软启动新品是买量
    # 调研最佳观察窗，不该只在平淡日露出）；**领导卡维持仅平淡日**（quiet_day 才渲染，
    # 保 2026-07-03「卡太单薄」反馈的填充行为不回退；非平淡日不进领导卡=减量宪法：
    # 早鸟未过 ST 口径核实，真上榜后走正常检出通道进领导卡）。
    if radar_items and (not is_leader or quiet_day):
        radar_lines = build_radar_recent_lines(radar_items, cap, articles=articles)
        if radar_lines:
            total += len(radar_lines)
            sections.append(
                "【🛒 商店雷达 · 近期新上架】（厂商开发者账号清单 diff · 含软启动）"
                "\n\n" + "\n\n".join(radar_lines))
    # 平淡日「SLG 行业动态」兜底段（公众号广搜，见 send_daily_digest 的平淡日闸门）：
    # **非我方追踪竞品**的行业面背景，故独立段 + 明确标注。#178 上线时仅维护者卡（领导卡
    # 保持已核实竞品口径）；2026-07-03 应领导反馈「卡太单薄」改为**两卡都发**——段头
    # 「非我方追踪竞品」标注保留，口径边界靠标注而非删段。放全卡最后。
    if industry_articles:
        ind_lines = build_industry_lines(industry_articles, cap)
        if ind_lines:
            total += len(ind_lines)
            sections.append(
                "【📰 SLG 行业动态】（公众号近期 · 行业面背景，非我方追踪竞品）"
                "\n\n" + "\n\n".join(ind_lines))
    # 探测层玩法门控的折叠行（no silent caps）：仅维护者、且卡里已有别的内容才挂
    # ——空卡不因一条审计行变非空（否则会顶掉真平淡日的心跳/静默语义）。
    # ≤3 条带名字+分类（人眼可抓「真 SLG 被 LLM 误判滤掉」的误杀），更多只计数防刷屏。
    if probe_filtered and not is_leader and sections:
        if len(probe_filtered) <= 3:
            det = "、".join(f"{_md_name(x.get('name') or '?', maxlen=20)}"
                           f"（{x.get('subgenre') or '未分类'}）" for x in probe_filtered)
            sections.append(f"> 🧹 探测层玩法门控已滤：{det}")
        else:
            sections.append(f"> 🧹 探测层玩法门控：雷达 / RSS 早鸟共滤除 "
                            f"**{len(probe_filtered)}** 个非 SLG / 未分类新包")
    if not sections:
        return None
    head = f"### 📡 SLG 每日情报 · {today}"
    # 领导卡 TL;DR 不计「待建档 N」（那段已剥离）。对标命中数（⚔️）两卡都计，置顶提威胁面。
    # 🎬 计数取**正文实际渲染出的视频行数**（数 sections 里的 _VIDEO_SEG_PREFIX），不是
    # video_items 全量：视频只内联在新品 / 雷达行上，领导卡剥离「待识别新厂」新品、全局封顶
    # 截断都会让部分行不出现，用全量长度会让收卡方照 TL;DR 去卡里找 🎬 却找不到
    # （2026-07-19 实测：两卡 TL;DR 均报「视频 4」，正文实际只有 2 条）。与「待建档 N」按
    # 受众剥离同一口径：TL;DR 只报**本卡看得见**的东西。
    videos_shown = sum(s.count(_VIDEO_SEG_PREFIX) for s in sections)
    # ⚔️ 同赛道（TL;DR 置顶那项）同理只算**本卡带得出 ⚔️ 标记**的竞品：own_matches 是全局
    # 字典，命中的竞品可能落在领导卡剥离的层、或 free 层 is_slg 门控挡掉的行上（正文压根不
    # 渲染）——2026-07-19 实测两卡均报「同赛道 3」，正文实际只有 1 个 app 带 ⚔️，另 2 个是
    # 下载榜 is_slg=false 噪声。复用 _collect_scored_items 的可见性口径（它已做 is_reentry
    # 过滤 + free 层 is_slg 门控），与上面两项一致：TL;DR 只报**本卡看得见**的东西。
    own_shown = len(set(own_matches or {})
                    & {it["e"].get("app_id")
                       for _, it in _collect_scored_items(per_combo, own_matches)})
    tldr = _digest_tldr(per_combo, version_changes, region_changes, videos_shown,
                        None if is_leader else lead_items,
                        own_match_count=own_shown)
    # 「今日重点」一行索引：按市场指出该先看哪儿（见 build_highlight_index）。与上面按
    # 事件类型汇总的 TL;DR 正交，两行都不复述正文内容。
    hi_index = build_highlight_index(per_combo, settings.DIGEST_HIGHLIGHTS_TOPN, own_matches)
    hi_section = [f"> {hi_index}"] if hi_index else []
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
            "> 核心市场（美国 · iOS）今日榜单**未同步**，日报暂缺。\n\n"
            "> 可能是 Sensor Tower 配额烧穿或同步任务失败——请查同步日志 / 配额水位。")
    return "每日情报 · 数据未就位", text


async def _leader_digest_sent_today(date: str) -> bool:
    """领导群今日 digest 是否已发（幂等标记，防 misfire 补跑重复推领导群）。date=UTC 日。"""
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(LeaderDigestSend.id).where(LeaderDigestSend.send_date == date)
        )).first()
    return row is not None


async def _mark_leader_digest_sent(date: str, content: str) -> None:
    """领导群发送**成功后**落幂等标记（失败不落，下轮可重试）。send_date 唯一约束兜并发/
    竞态（理论 max_instances=1 无并发）：重复插入 IntegrityError 吞掉即可。"""
    import hashlib
    h = hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:32]
    async with AsyncSessionLocal() as db:
        db.add(LeaderDigestSend(send_date=date, content_hash=h))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()


async def _load_sent_article_links() -> set[str]:
    """行业动态段跨天去重：台账里已推过的文章 link 集合（全量，prune 已控表规模）。
    关开关（WECHAT_ARTICLE_DEDUP_ENABLED=False）→ 空集，退回仅时窗控重复的旧行为。"""
    if not settings.WECHAT_ARTICLE_DEDUP_ENABLED:
        return set()
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(WechatArticleSent.link))).scalars().all()
    return set(rows)


async def _mark_articles_sent(articles: list, today: str) -> None:
    """行业动态段**发送成功后**把本次展示的文章 link 落台账（跨天去重），并 prune 掉超
    retention 天的老行。link 唯一：已存在的重复插入吞掉、保留首推日。today=UTC 日。"""
    if not settings.WECHAT_ARTICLE_DEDUP_ENABLED or not articles:
        return
    async with AsyncSessionLocal() as db:
        for a in articles:
            link = getattr(a, "link", None)
            if not link:
                continue
            db.add(WechatArticleSent(link=link,
                                     title=(getattr(a, "title", "") or "")[:300],
                                     first_sent_date=today))
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()   # link 已在台账（同篇文章前几天推过）→ 保留首推日
        days = settings.WECHAT_ARTICLE_SENT_RETENTION_DAYS
        if days > 0:
            cutoff = (datetime.strptime(today, "%Y-%m-%d")
                      - timedelta(days=days)).strftime("%Y-%m-%d")
            await db.execute(delete(WechatArticleSent)
                             .where(WechatArticleSent.first_sent_date < cutoff))
            await db.commit()


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
    # 存量竞品子品类回补 drain（P1-2）：给「有描述的 is_slg 存量 app」（tracked / movement
    # 老熟人 / subgenre 特性前老检出行）补分类进 app_subgenre，让下方 own_matches 的 ⚔️ 同赛道
    # 对老竞品也生效。放 translate 之后（同一 LLM 网关）、own_matches 之前，当轮分类当轮见效；
    # 封顶前进式累积，几天分类完存量。零 ST；无 key / mock no-op。
    from app.services.app_subgenre import classify_pending_app_subgenres
    try:
        await classify_pending_app_subgenres()
    except Exception:
        logger.exception("App subgenre backfill (in digest) crashed")
    # 视频补漏 drain：02:45 的视频 job 在 subgenre_cn 写入（就在上面的 translate）之前跑，
    # 「非追踪厂商但题材是 SLG」的当日新品在那轮被 SLG 门控跳过（review #181 发现）。
    # translate 刚写完题材分类，此刻补一轮 drain 让这类新品的视频赶上当日卡。台账去重
    # 保证已搜的 app 零重复 YT 调用；YT key 未配则整体 no-op。放 webhook 闸门之前——
    # 前端抽屉的视频段同样受益，不依赖 webhook。
    from app.services.newcomer_video import sync_newcomer_videos
    try:
        await sync_newcomer_videos()
    except Exception:
        logger.exception("Newcomer video drain (in digest) crashed")
    # RSS 早鸟（ADR 0005）：拉次市场当日 RSS 策略畅销榜 diff 台账，真早鸟落影子行
    # （riding 富化/翻译/视频管道）。放 webhook 闸门**之前**——台账/影子行不依赖
    # webhook；返回的 items 给下方维护者卡「⚡ RSS 早鸟」段（misfire 补跑台账已见 →
    # items 空 → 不重复推）。零 ST；失败静默降级（旧版 RSS 随时可能退役）。
    rss_earlybird_items: list[dict] = []
    probe_filtered: list[dict] = []  # 探测层（RSS+雷达）玩法门控滤除明细 → 维护者卡折叠行
    try:
        from app.services.rss_earlybird import sync_rss_earlybird
        rss_earlybird_items = (await sync_rss_earlybird()).get("items") or []
        # 产品级 SLG 门控（2026-07-16）：LLM 玩法分类 ∈ 核心口径才推（非 SLG / 未分类滤）。
        rss_earlybird_items, _rss_cut = await _slg_gate_probe_items(rss_earlybird_items)
        probe_filtered.extend(_rss_cut)
    except Exception:
        logger.exception("RSS earlybird sync (in digest) crashed")
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
    # 搜文章关键词改 post-loop 从 per_combo 统一挑（_newcomer_search_keywords，按优先级
    # 确定性截断），不再循环里往 set 里塞——治 set 截断非确定 + reentry 占额。
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
            if publisher.get("as_of") == today:
                # 真实上架日门控：剔除老产品（本地"首次出现"≠ 真新品）——与 /publishers
                # 端点同一 helper、同口径，避免 2013–2017 老 SLG 被当新品推给领导。
                publisher["newcomers"] = await gate_publisher_newcomers_by_release_date(
                    publisher.get("newcomers") or [], country, platform)
                entry["publisher"] = publisher
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
                if f_publisher.get("as_of") == today:
                    f_publisher["newcomers"] = await gate_publisher_newcomers_by_release_date(
                        f_publisher.get("newcomers") or [], country, platform)
                    entry["free_publisher"] = f_publisher
        except Exception:
            logger.exception("daily digest detection failed for %s/%s", country, platform)
        per_combo.append(entry)

    # is_slg 跨 combo OR 传播（拼卡前统一回写）：同一 app_id 任一 combo/层判 SLG，或
    # log 记忆/tracked 曾判 SLG → 该 app 全部行置 True。治本地化 publisher 串导致的跨
    # combo 分裂（Last Furry KR=1/JP=0 实锤）——领导卡 market 过滤、下载榜 is_slg 门控、
    # 打分收集、关键词挑选都按行读 is_slg，这里一处回写全局生效。零外呼。
    try:
        from app.services.newcomer_log import slg_app_ids_known
        _layers = _NEWCOMER_SOURCE_KEYS
        _all_ids: set = set()
        _slg_ids: set = set()
        for c in per_combo:
            for key in _layers:
                for n in ((c.get(key) or {}).get("newcomers") or []):
                    if aid := n.get("app_id"):
                        _all_ids.add(aid)
                        if n.get("is_slg"):
                            _slg_ids.add(aid)
        _slg_ids |= await slg_app_ids_known(_all_ids - _slg_ids)
        for c in per_combo:
            for key in _layers:
                for n in ((c.get(key) or {}).get("newcomers") or []):
                    if n.get("app_id") in _slg_ids:
                        n["is_slg"] = True
    except Exception:
        logger.exception("is_slg cross-combo propagation failed (digest continues)")

    # 硬锚核心 US/iOS：区分『真平淡日』(已同步、确无事) vs『数据未就位』(今日无快照=同步可能失败)。
    # detect_movement 仅在 today 未缺数据时赋值 entry["movement"]（today_missing 闸门），故它非 None
    # 或 market.as_of==today 即核心 combo 今日有新快照。找不到该 combo（理论不应）→ 保守按已就位、不误报。
    # （定义提前：雷达每日拉取 + 平淡日兜底闸门都要用它，确保填充不掩盖『数据未就位』。）
    def _core_synced() -> bool:
        for c in per_combo:
            if c["country"] == "US" and c["platform"] == "ios":
                return c.get("movement") is not None or (c.get("market") or {}).get("as_of") == today
        return True

    # 商店雷达近期新上架（ADR 0006 切片2）：**每日拉取**（原仅平淡日兜底）——上榜前早探测层，
    # 维护者卡有则即显；领导卡仍仅平淡日渲染（audience 路由在 build_daily_digest 的 quiet_day
    # 判断）。仍 gate 在 _core_synced：管道故障日不得被雷达段填成非空卡、掩盖『数据未就位』
    # 告警（与平淡日填充同坑同防）。本地 publisher_itunes_apps 读取，零 ST。
    radar_items: list = []
    if (settings.DIGEST_RADAR_RECENT_DAYS > 0 and not settings.USE_MOCK_DATA
            and _core_synced()):
        try:
            radar_items = await _recent_radar_arrivals(settings.DIGEST_RADAR_RECENT_DAYS)
            # 产品级 SLG 门控（2026-07-16，与 RSS 早鸟同口径）：厂商级 is_slg 挡不住
            # SLG 大厂出的非 SLG 新品（Plarium→LegendUP 放置 RPG 实证）。
            radar_items, _radar_cut = await _slg_gate_probe_items(radar_items)
            probe_filtered.extend(_radar_cut)
        except Exception:
            logger.warning("radar recent arrivals failed", exc_info=True)

    # 批量搜微信文章：关键词 = 当日新品（收入榜 + 下载榜两层）**游戏名**，按优先级确定性
    # 截断（_newcomer_search_keywords）。只用游戏名不用厂商名——文章回挂走 _name_matches
    # 按游戏名匹配，厂商名搜来的文章除非含游戏名否则挂不上，拿厂商名当词只会烧配额搜回不来。
    articles_by_app: dict = {}
    keywords = _newcomer_search_keywords(per_combo, settings.WECHAT_MAX_KEYWORDS)
    # 雷达新品名也进搜索面（ADR 0006 切片2：雷达行此前只有 🎬 没 📰）——排在榜单新品之后、
    # 共用同一 WECHAT_MAX_KEYWORDS 预算（榜单新品优先，预算紧时雷达名先被截掉）。
    for it in radar_items:
        nm = it.get("name")
        if nm and nm not in keywords and len(keywords) < settings.WECHAT_MAX_KEYWORDS:
            keywords.append(nm)
    if keywords and settings.WECHAT_ENABLED and not settings.USE_MOCK_DATA:
        try:
            from app.services.wechat_articles import search_multi_keywords
            article_list = await search_multi_keywords(keywords, limit=20)
            articles_by_app = _match_articles_to_apps(per_combo, article_list,
                                                      extra_rows=radar_items)
        except Exception:
            logger.warning("wechat articles search failed", exc_info=True)

    # 厂商主体中文归属：复用循环外已加载的 matchers，解析市场新面孔 / 下载榜新品 / 异动行
    # 涉及的 app_id。厂商新品行自带 entity_name，这里补其余层（纯内存匹配，零查询/零配额）。
    # **free 两层曾漏过**（2026-07-19 修）：下载榜新品没进这里 → 渲染时
    # `entity_name or entities.get(aid) or publisher` 一路回退到 ST 原始英文串，于是**有中文
    # 档案的厂商也显示英文**（莉莉丝 id=6 挂着 lilith/lilithgames 两个 alias，卡上却是
    # "Lilith Games"）。领导反馈「非中文元素太多」，这是其中一个可直接消除的来源。
    entities_by_app: dict = {}
    try:
        for c in per_combo:
            mv = c.get("movement") or {}
            rows = []
            for key in ("market", "free_market", "free_publisher"):
                rows += list((c.get(key) or {}).get("newcomers") or [])
            for k in _MOVEMENT_KEYS:
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
        for key in _NEWCOMER_SOURCE_KEYS:
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
                video_items.append({"app_id": aid, "name": newcomer_apps[aid],
                                    "count": len(vlist), "url": vlist[0].url})
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
        cand = collect_lead_candidates(per_combo)
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
        # 候选竞品（新品三段 + movement）app_id 全集。
        cand_ids: set[str] = set()
        for c in per_combo:
            for key in _NEWCOMER_SOURCE_KEYS:
                for n in ((c.get(key) or {}).get("newcomers") or []):
                    if n.get("app_id"):
                        cand_ids.add(n["app_id"])
            mv = c.get("movement") or {}
            for k in _MOVEMENT_KEYS:
                for e in (mv.get(k) or []):
                    if e.get("app_id"):
                        cand_ids.add(e["app_id"])
        # 候选竞品子品类：market_newcomer_log 优先 + app_subgenre fallback（P1-2 存量回补，
        # 让 ⚔️ 同赛道对 movement 老竞品也生效）。零 ST。
        subgenre_by_app = await _subgenres_for_apps(cand_ids)
        # C 可读性：把中文子品类富化进各 entry，供 _sg_label 渲染（外文名一眼辨品类）。
        # 与 own_matches 解耦——无 own_products 也要显示子品类标签。
        for c in per_combo:
            for key in _NEWCOMER_SOURCE_KEYS:
                for n in ((c.get(key) or {}).get("newcomers") or []):
                    if (aid := n.get("app_id")) and subgenre_by_app.get(aid):
                        n["subgenre_cn"] = subgenre_by_app[aid]
            mv = c.get("movement") or {}
            for k in _MOVEMENT_KEYS:
                for e in (mv.get(k) or []):
                    if (aid := e.get("app_id")) and subgenre_by_app.get(aid):
                        e["subgenre_cn"] = subgenre_by_app[aid]
        # 「同赛道」匹配（需 own_products）：文本=名字 + LLM 摘要，子品类优先精确匹配。
        own_products = await _load_own_products()
        if own_products:
            for c in per_combo:
                for key in _NEWCOMER_SOURCE_KEYS:
                    for n in ((c.get(key) or {}).get("newcomers") or []):
                        aid = n.get("app_id")
                        if aid and not n.get("is_reentry") and aid not in own_matches:
                            text = " ".join(t for t in (n.get("name"), summaries_by_app.get(aid)) if t)
                            if (m := _match_own_product(text, subgenre_by_app.get(aid), own_products)):
                                own_matches[aid] = m[0]
                mv = c.get("movement") or {}
                for k in _MOVEMENT_KEYS:
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

    # 平淡日行业动态兜底（两卡都发，2026-07-03 起；#178 上线时仅维护者卡）：当日竞品实质
    # 信号 < DIGEST_QUIET_THRESHOLD **且核心已同步**（真平淡、非管道故障）→ 补 A 行业动态段
    # （公众号广搜，零 ST）。gate 在 _core_synced 上是关键：否则同步故障日会被填成非空卡、
    # 掩盖『数据未就位』。（雷达段已升级为每日拉取，见上方 ADR 0006 切片2 块；is_quiet 仍
    # 决定领导卡是否渲染雷达段 = 原平淡日填充行为。）
    industry_articles: list = []
    is_quiet = (settings.DIGEST_QUIET_THRESHOLD > 0
                and _primary_item_count(per_combo, version_changes, region_changes)
                    < settings.DIGEST_QUIET_THRESHOLD
                and not settings.USE_MOCK_DATA and _core_synced())
    if is_quiet:
        # A：SLG 行业动态（公众号广搜，零 ST；未启用/连不上降级空）。跨天重复靠时窗控。
        if settings.WECHAT_INDUSTRY_ENABLED and settings.WECHAT_ENABLED:
            try:
                # 每号拉一次列表 + 本地关键词匹配（避免 词×号 笛卡尔积并发打到 wechat-api
                # 限流、把阿杜聊游戏/金角游戏这类活跃号的料吞掉，见 search_industry_articles）。
                from app.services.wechat_articles import search_industry_articles
                ind_kws = [k.strip() for k in settings.WECHAT_INDUSTRY_KEYWORDS.split(",") if k.strip()]
                if ind_kws:
                    raw = await search_industry_articles(ind_kws, limit=settings.WECHAT_INDUSTRY_MAX,
                                                         days=settings.WECHAT_INDUSTRY_DAYS)
                    # 去掉①已按新品名精确挂上的文章（免与新品行 📰 重复，同卡内）+ ②往日已推过的
                    # 文章（跨天去重台账，让领导群每天见到没推过的）。
                    shown = {a.link for arts in articles_by_app.values() for a in arts}
                    sent_before = await _load_sent_article_links()
                    industry_articles = [a for a in raw if a.link not in shown
                                         and a.link not in sent_before][:settings.WECHAT_INDUSTRY_MAX]
            except Exception:
                logger.warning("wechat industry search (quiet-day filler) failed", exc_info=True)

    def _render(audience):
        return build_daily_digest(per_combo, today, articles=articles_by_app,
                                  entities=entities_by_app, version_changes=version_changes,
                                  video_items=video_items, region_changes=region_changes,
                                  summaries=summaries_by_app, lead_items=lead_items,
                                  audience=audience, own_matches=own_matches,
                                  industry_articles=industry_articles, radar_items=radar_items,
                                  rss_items=rss_earlybird_items, quiet_day=is_quiet,
                                  probe_filtered=probe_filtered)

    sent_any = False
    # job 心跳自检（P1②）：关键定时 job 有成功记录却超期 → 维护者卡尾 ⚠️（补静默失败盲区，
    # A3 前科）。**仅维护者卡**；即便平淡日 / 数据未就位也要让告警出得去（见下 None 分支）。
    from app.services.job_heartbeat import get_stale_jobs, render_stale_alert
    _stale_alert = render_stale_alert(await get_stale_jobs())
    msg_m = _render("maintainer")
    if msg_m is not None and _stale_alert:
        msg_m = (msg_m[0], msg_m[1] + "\n\n---\n\n" + _stale_alert, msg_m[2])
    if msg_m is None:
        if _stale_alert:
            # 平淡日 / 无内容但有 job 超期：单独发维护者告警卡（不进领导群），别让静默失败被
            # 「平淡=平静」掩盖。critical=True：告警卡自身发失败也升 Sentry。
            sent_any = await dingtalk.send_markdown(
                f"任务自检 · {today}", "### ⚠️ 定时任务自检\n\n" + _stale_alert,
                target="maintainer", critical=True) or sent_any
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
            # 领导群每天最多推一次（含心跳）：misfire 补跑当天已发则跳过。
            if dingtalk.leader_target_configured() and not await _leader_digest_sent_today(today):
                if await dingtalk.send_markdown(*hb, target="leader"):
                    await _mark_leader_digest_sent(today, hb[1])
                    sent_any = True
            return sent_any
        logger.info("daily digest: nothing to report for %s (core synced, quiet day)", today)
        return sent_any
    sent_any = await dingtalk.send_action_card(*msg_m, target="maintainer",
                                               critical=True) or sent_any
    if dingtalk.leader_target_configured():
        # 领导群每天最多推一次：misfire 补跑（容器在 03:00–04:00 UTC 重启触发 daily_alert_digest
        # 重跑）当天已发就跳过，不重复推领导群。维护者群不设限（上面已发、运维向重发无碍）。
        # 标记在**发送成功后**才落（失败不落、下轮可重试）。
        if await _leader_digest_sent_today(today):
            logger.info("daily digest: leader already pushed for %s, skip duplicate (misfire re-run?)", today)
        else:
            msg_l = _render("leader")
            if msg_l is not None and await dingtalk.send_action_card(
                    *msg_l, target="leader", critical=True):
                await _mark_leader_digest_sent(today, msg_l[1])
                sent_any = True
    # 行业动态段跨天去重：卡发出去了（任一群成功）就把本次展示的行业文章 link 落台账，
    # 下次广搜过滤掉。仅平淡日非空；发送失败(sent_any=False)不落，下轮还能展示。
    if sent_any and industry_articles:
        await _mark_articles_sent(industry_articles, today)
    return sent_any


# ── 新品周察周报卡（P0-1③ 新品生命周期追踪）────────────────────────────────

async def _collect_slg_newcomer_reps(days: int) -> list:
    """近 days 天检出的 SLG 新品，按 app_id 取一条代表行（周察卡 + 月度 rollup 共用）。

    只看 SLG——live is_slg（新接入厂商旧行也认得，#181 教训）OR 存档聚合（任一行存过 1
    即全 app 算 SLG——本地化 publisher 串 live 命不中时靠别的 combo 的判定，治跨 combo 分裂
    漏报）；跨 combo 按 app_id 取一条代表行（优先 US/iOS 主市场，否则最早检出——rows 已按
    first_detected 升序）。返回 rep_rows（MarketNewcomerLog 标量行，跨 session 读安全），
    空 = 窗口内无 SLG 新品。
    """
    from app.models.newcomer import MarketNewcomerLog
    from app.models.game import CHART_GROSSING
    from app.services.slg_publishers import is_slg

    since = utcnow_naive() - timedelta(days=days)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(MarketNewcomerLog).where(
                MarketNewcomerLog.chart_type == CHART_GROSSING,
                MarketNewcomerLog.first_detected_at >= since,
            ).order_by(MarketNewcomerLog.first_detected_at.asc())
        )).scalars().all()
    archived_slg = {r.app_id for r in rows if r.is_slg}
    reps: dict[str, "MarketNewcomerLog"] = {}
    for r in rows:
        if not (r.app_id in archived_slg or is_slg(r.app_id, r.publisher)):
            continue
        cur = reps.get(r.app_id)
        if cur is None:
            reps[r.app_id] = r
        elif (r.country, r.platform) == ("US", "ios") and (cur.country, cur.platform) != ("US", "ios"):
            reps[r.app_id] = r
    return list(reps.values())


async def build_weekly_newcomer_review(days: int, cap: int) -> Optional[tuple[str, str]]:
    """近 days 天检出的 SLG 新品「存活/爬升/掉榜」周察卡（读时算 game_rankings，零 ST）。

    补「新品检出即阅后即焚」断层：除非冲进收入榜 Top20（movement），否则没人知道检出后
    它起飞还是死了。这里把近窗口 SLG 新品按检出后走势分层——🚀 起飞（名次持续上升）/
    ✅ 在榜存活 / ✝️ 掉榜（昙花一现），起飞 + 掉榜两段列明细（决策上最该看的两极）。

    返回 (title, text)；窗口内无 SLG 新品 → None（不发空卡）。
    """
    from app.services.newcomers import compute_trajectories

    rep_rows = await _collect_slg_newcomer_reps(days)
    if not rep_rows:
        return None
    traj = await compute_trajectories(rep_rows)
    # C 可读性（#198 同款）：中文玩法子品类标签，让外文新品名一眼可辨品类（数字门SLG/塔防/…），
    # 顺带暴露误标（塔防等非 SLG 混进来时一眼可见）。
    subgenre_by_app = await _subgenres_for_apps({r.app_id for r in rep_rows})
    # 晋升 push 触发（用户裁定：0 晋升是入口摩擦非刻意）：起飞段给未 tracked 的候选加
    # 「⭐ 建议转深度追踪」+ 看板深链——climbing 信号系统早算出来了，只是没接行动入口。
    # 周察卡是维护者卡，不动领导卡（减量宪法）。
    from app.models.game import Game
    async with AsyncSessionLocal() as db:
        tracked_ids = set((await db.execute(select(Game.app_id))).scalars().all())
    climbing: list[dict] = []
    surviving = dropped = 0
    for r in rep_rows:
        tj = traj.get(r.id) or {}
        trend = tj.get("trend")
        if trend == "climbing":
            climbing.append({"name": r.name or r.app_id, "detect_rank": r.rank,
                             "app_id": r.app_id,
                             **tj, "subgenre_cn": subgenre_by_app.get(r.app_id)})
        elif trend == "dropped":
            dropped += 1
        elif tj.get("on_chart"):
            surviving += 1
        # new / unknown（刚检出 / 无轨迹点）计入总数、不进明细分层

    # 掉榜明细单列（值得注意的「昙花一现」）：峰值靠前的优先。
    dropped_items = sorted(
        ({"name": r.name or r.app_id, "detect_rank": r.rank,
          **(traj.get(r.id) or {}), "subgenre_cn": subgenre_by_app.get(r.app_id)}
         for r in rep_rows if (traj.get(r.id) or {}).get("trend") == "dropped"),
        key=lambda x: (x.get("peak_rank") is None, x.get("peak_rank") or 999),
    )
    total = len(rep_rows)
    today = utcnow_naive().strftime("%Y-%m-%d")
    head = (f"### 📈 SLG 新品周察 · 近 {days} 天\n\n"
            f"> 近 {days} 天新上架的 SLG 竞品检出后走势如何——哪些在往畅销榜头部冲、哪些已凉。"
            f"名次 = 畅销榜排名，越小越靠前。\n\n"
            f"检出 **{total}** 款 SLG 新品：🚀 起飞 {len(climbing)} · "
            f"✅ 在榜 {surviving} · ✝️ 掉榜 {dropped}")
    sections = [head]

    if climbing:
        # 起飞按累计升幅降序（升幅 = 检出名次 - 当前名次，越大越靠前）。
        climbing.sort(key=lambda x: (x["detect_rank"] or 999) - (x.get("current_rank") or 999),
                      reverse=True)
        lines = ["\n\n**🚀 起飞（畅销榜名次持续上冲）**"]
        for x in climbing[:cap]:
            gain = (x["detect_rank"] or 0) - (x.get("current_rank") or 0)
            span = f"{x['days_tracked']} 天涨 {gain} 名" if x.get("days_tracked") else f"涨 {gain} 名"
            # 未 tracked 的起飞新品 = 最佳晋升时机：深链直达看板卡片（focus 高亮，
            # 抽屉里就是一键晋升），「看到→行动」压到两步。已 tracked 的不唠叨。
            promote = ""
            if x.get("app_id") and x["app_id"] not in tracked_ids:
                url = _dashboard_focus_url(x["app_id"], "market")
                promote = (f" · [⭐ 建议转深度追踪]({url})" if url
                           else " · ⭐ 建议转深度追踪")
            lines.append(f"- **{_md_name(x['name'])}**{_sg_label(x)} · "
                         f"#{x['detect_rank']} → **#{x.get('current_rank')}**，{span}{promote}")
        sections.append("\n".join(lines))

    if dropped_items:
        lines = ["\n\n**✝️ 已掉榜（昙花一现）**"]
        for x in dropped_items[:cap]:
            pk = f" · 最高冲到 #{x['peak_rank']}" if x.get("peak_rank") else ""
            ls = f" · 末次在榜 {x['last_seen']}" if x.get("last_seen") else ""
            lines.append(f"- **{_md_name(x['name'])}**{_sg_label(x)} · 检出时 #{x['detect_rank']}{pk}{ls}")
        sections.append("\n".join(lines))

    return f"SLG 新品周察 {today}", "".join(sections)


async def send_weekly_newcomer_review() -> bool:
    """周级 job 入口：拼一张「SLG 新品周察」卡，**仅维护者群**。

    2026-07-06 用户裁定：这是分析/趋势向的生命周期分层卡（近 N 天几十款新品的起飞/在榜/
    掉榜），不是已核实竞品的当日动态——领导群只保留每日 digest（movement + 新品情报），不收
    这张，避免领导注意力被分析卡稀释。此前（#188 上线时）两群都发。

    未配维护者 webhook / 未开开关 / 窗口内无 SLG 新品 → 静默 no-op。无幂等台账（周级、
    misfire 重发无实质危害，与每日必达卡的领导群幂等守卫不同轴，故不引新表）。零 ST。
    """
    if not settings.DIGEST_WEEKLY_REVIEW_ENABLED:
        return False
    if not dingtalk.is_enabled():
        return False
    card = await build_weekly_newcomer_review(
        settings.DIGEST_WEEKLY_REVIEW_DAYS, settings.DIGEST_WEEKLY_REVIEW_CAP)
    # 白名单卫生自检（2026-07-16）：pin/alias 建档判断 × LLM 玩法分类交叉审计——把
    # 「下一个 CyberJoy」（降级漏删 pin / 多品类小厂整档误入）的发现从事故驱动变自检
    # 驱动。失败静默不拖垮周察卡；有发现时随周察卡尾段发，周察卡为 None 也单独发
    # （审计发现不能被「本周无新品」吞掉）。仅维护者（建档运维向）。
    audit_lines: list[str] = []
    try:
        from app.services.publisher_audit import audit_whitelist_hygiene, build_audit_lines
        audit_lines = build_audit_lines(await audit_whitelist_hygiene())
    except Exception:
        logger.warning("whitelist hygiene audit failed", exc_info=True)
    if card is None and not audit_lines:
        logger.info("weekly newcomer review: no SLG newcomers in window, skip")
        return False
    audit_section = ("\n\n---\n\n**🧭 白名单卫生自检**（建档判断 × LLM 玩法分类矛盾，"
                     "请人工复核）\n\n" + "\n".join(audit_lines)) if audit_lines else ""
    if card is None:
        today = utcnow_naive().strftime("%Y-%m-%d")
        return await dingtalk.send_markdown(
            f"SLG 新品周察 {today}",
            f"### 📈 SLG 新品周察 · {today}\n\n> 本周窗口内无 SLG 新品。" + audit_section,
            target="maintainer")
    return await dingtalk.send_markdown(card[0], card[1] + audit_section,
                                        target="maintainer")


# ── 月度市场复盘 rollup（补 digest 阅后即焚、无复利视图断层）──────────────────

async def _monthly_rank_movers(days: int, cap: int, exclude_app_ids: Optional[set] = None):
    """近 days 天 US 收入榜里 SLG 竞品的名次净变动（读 game_rankings，零 ST）。

    每个 app 取窗口内最早与最晚**有名次**的快照对比：net = 首名次 - 末名次（>0 = 名次
    前进/上升，因名次越小越靠前）。要求首末日历跨度 >= max(7, days//3)，否则窗口内点太少、
    月度趋势不可信 → 跳过（US 日更，够密的竞品才留）。只看 US/ios 主市场——日更数据最密、
    月度格局最可信；次市场双周快照太稀不入。exclude_app_ids = 近窗口检出的新品，它们的走势
    归段②「新品存活」，段①只看更老的既有竞品，避免同一 app 在两段重复。返回 (climbers,
    fallers)，各 [{name, app_id, start, end, net, subgenre_cn}]，按 |net| 降序、cap 截断。
    """
    from app.models.game import GameRanking, CHART_GROSSING
    from app.services.slg_publishers import is_slg

    exclude_app_ids = exclude_app_ids or set()
    since = (utcnow_naive() - timedelta(days=days)).strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        pts = (await db.execute(
            select(GameRanking.app_id, GameRanking.date, GameRanking.rank,
                   GameRanking.name, GameRanking.publisher)
            .where(GameRanking.country == "US", GameRanking.platform == "ios",
                   GameRanking.chart_type == CHART_GROSSING,
                   GameRanking.date >= since, GameRanking.rank.is_not(None))
        )).all()

    by_app: dict[str, dict] = {}
    for aid, d, rk, name, pub in pts:
        e = by_app.setdefault(aid, {"points": [], "name": None, "publisher": None})
        e["points"].append((d, rk))
        if name and not e["name"]:
            e["name"] = name
        if pub and not e["publisher"]:
            e["publisher"] = pub

    min_span = max(7, days // 3)
    movers: list[dict] = []
    for aid, e in by_app.items():
        if aid in exclude_app_ids or not is_slg(aid, e["publisher"]):
            continue
        pts_sorted = sorted(e["points"])
        (first_d, first_rk), (last_d, last_rk) = pts_sorted[0], pts_sorted[-1]
        span = (datetime.strptime(last_d, "%Y-%m-%d")
                - datetime.strptime(first_d, "%Y-%m-%d")).days
        if span < min_span:
            continue
        net = first_rk - last_rk
        if net == 0:
            continue
        movers.append({"name": e["name"] or aid, "app_id": aid,
                       "start": first_rk, "end": last_rk, "net": net})

    subgenre_by_app = await _subgenres_for_apps({m["app_id"] for m in movers})
    for m in movers:
        m["subgenre_cn"] = subgenre_by_app.get(m["app_id"])
    climbers = sorted((m for m in movers if m["net"] > 0), key=lambda x: -x["net"])[:cap]
    fallers = sorted((m for m in movers if m["net"] < 0), key=lambda x: x["net"])[:cap]
    return climbers, fallers


async def _monthly_newcomer_survival(rep_rows: list, cap: int):
    """近窗口检出的 SLG 新品存活分层小结（复用 compute_trajectories，零 ST）。

    rep_rows 由调用方传入（build 已查一次、段①段②共用，省一次 DB 读）。返回
    (counts, climbing_top) 或 None（无 SLG 新品）：counts = {total, climbing, surviving,
    dropped}；climbing_top = 起飞明细（累计升幅降序、cap 截断）。月度只给分布数 + 起飞明细
    （决策上最该看的），不逐条列掉榜（那是周察卡的活）。
    """
    from app.services.newcomers import compute_trajectories

    if not rep_rows:
        return None
    traj = await compute_trajectories(rep_rows)
    subgenre_by_app = await _subgenres_for_apps({r.app_id for r in rep_rows})
    climbing: list[dict] = []
    surviving = dropped = 0
    for r in rep_rows:
        tj = traj.get(r.id) or {}
        trend = tj.get("trend")
        if trend == "climbing":
            climbing.append({"name": r.name or r.app_id, "detect_rank": r.rank,
                             **tj, "subgenre_cn": subgenre_by_app.get(r.app_id)})
        elif trend == "dropped":
            dropped += 1
        elif tj.get("on_chart"):
            surviving += 1
    climbing.sort(key=lambda x: (x["detect_rank"] or 999) - (x.get("current_rank") or 999),
                  reverse=True)
    return ({"total": len(rep_rows), "climbing": len(climbing),
             "surviving": surviving, "dropped": dropped}, climbing[:cap])


async def build_monthly_market_rollup(days: int, cap: int) -> Optional[tuple[str, str]]:
    """月度市场复盘卡：名次净变动 + 新品存活小结 + 赛道升降温（读时算本地库，零 ST）。

    补 digest 阅后即焚、无复利视图断层——回答「这个月 SLG 市场发生了什么」。三段都无内容
    → None（不发空卡）。仅维护者群（见 send_monthly_market_rollup）。返回 (title, text)。
    """
    rep_rows = await _collect_slg_newcomer_reps(days)
    newcomer_ids = {r.app_id for r in rep_rows}
    # 段①排除近窗口新品（它们归段②「新品存活」），避免同一 app 在两段重复出现。
    climbers, fallers = await _monthly_rank_movers(days, cap, exclude_app_ids=newcomer_ids)
    survival = await _monthly_newcomer_survival(rep_rows, cap)
    from app.services.newcomers import compute_subgenre_pulse
    _pulse_total, pulse_buckets = await compute_subgenre_pulse(days)
    if not climbers and not fallers and survival is None and not pulse_buckets:
        return None

    today = utcnow_naive().strftime("%Y-%m-%d")
    sections = [f"### 🗓️ SLG 市场月报 · 近 {days} 天\n\n"
                f"> 这段时间 SLG 竞品格局怎么变了——US 收入榜名次谁涨谁跌、新上架的新品活下来几个、"
                f"哪些赛道在升温。名次 = 畅销榜排名，越小越靠前。"]

    if climbers or fallers:
        lines = ["\n\n**📊 名次净变动（US 收入榜）**"]
        if climbers:
            lines.append("\n_上升_")
            for m in climbers:
                lines.append(f"- **{_md_name(m['name'])}**{_sg_label(m)} · "
                             f"#{m['start']} → **#{m['end']}**（↑{m['net']}）")
        if fallers:
            lines.append("\n_下降_")
            for m in fallers:
                lines.append(f"- **{_md_name(m['name'])}**{_sg_label(m)} · "
                             f"#{m['start']} → **#{m['end']}**（↓{-m['net']}）")
        sections.append("\n".join(lines))

    if survival is not None:
        counts, climbing_top = survival
        lines = [f"\n\n**🌱 新品存活（近 {days} 天检出 {counts['total']} 款 SLG 新品）**\n"
                 f"🚀 起飞 {counts['climbing']} · ✅ 在榜 {counts['surviving']} · "
                 f"✝️ 掉榜 {counts['dropped']}"]
        for x in climbing_top:
            gain = (x["detect_rank"] or 0) - (x.get("current_rank") or 0)
            span = f"{x['days_tracked']} 天涨 {gain} 名" if x.get("days_tracked") else f"涨 {gain} 名"
            lines.append(f"- **{_md_name(x['name'])}**{_sg_label(x)} · "
                         f"#{x['detect_rank']} → **#{x.get('current_rank')}**，{span}")
        sections.append("\n".join(lines))

    if pulse_buckets:
        lines = [f"\n\n**🎯 赛道升降温（近 {days} 天新品按玩法子品类，环比上一 {days} 天）**"]
        for b in pulse_buckets[:cap]:
            d = b["delta"]
            arrow = f"↑{d}" if d > 0 else (f"↓{-d}" if d < 0 else "→持平")
            lines.append(f"- **{b['subgenre']}** {b['count']} 款新品（{arrow}）")
        sections.append("\n".join(lines))

    return f"SLG 市场月报 {today}", "".join(sections)


async def send_monthly_market_rollup() -> bool:
    """月级 job 入口：拼一张「SLG 市场月报」卡，**仅维护者群**（同周察卡口径 / 减量宪法：
    领导群只保留每日 digest 已核实竞品动态，不收分析/复盘卡稀释注意力）。

    未开开关 / 未配维护者 webhook / 两段都无内容 → 静默 no-op。无幂等台账（月级、misfire
    重发无实质危害，与每日必达卡的领导群幂等守卫不同轴）。零 ST。
    """
    if not settings.DIGEST_MONTHLY_ROLLUP_ENABLED:
        return False
    if not dingtalk.is_enabled():
        return False
    card = await build_monthly_market_rollup(
        settings.DIGEST_MONTHLY_ROLLUP_DAYS, settings.DIGEST_MONTHLY_ROLLUP_CAP)
    if card is None:
        logger.info("monthly market rollup: no movers or newcomers in window, skip")
        return False
    return await dingtalk.send_markdown(*card, target="maintainer")


# ── 微信公众号登录过期提醒 ─────────────────────────────────────────────────

# ssh 隧道兜底（手机端外网受限、按钮打不开时改用电脑）；按钮才是主路径（看板登录页，
# 开页即实时二维码、免隧道）。保留 login.html 链接是有意的——按钮不可达时的退路。
_WECHAT_RELOGIN_FALLBACK = (
    "> 按钮打不开（手机端外网受限）？改用电脑：终端跑 "
    "`ssh -L 5050:127.0.0.1:5000 hk-prod`，再浏览器开 http://localhost:5050/login.html 扫码。"
)


def _wechat_login_btns() -> list[tuple[str, str]]:
    """「扫码续期」按钮 → 看板登录页（开页即实时二维码）。未配 DASHBOARD_BASE_URL 则无按钮
    （send_action_card 自动降级 markdown，仅留 ssh 兜底文案）。"""
    base = (settings.DASHBOARD_BASE_URL or "").rstrip("/")
    return [("🔑 点此扫码续期", f"{base}/wechat-login")] if base else []


def _wechat_alert_tier(status, now_ts: float, warn_days: int) -> Optional[str]:
    """登录态 → 提醒档位：expired（已失效）/ warn12（≤12h）/ warn24（≤warn_days*24h）/ None。
    纯函数，供去重判级用（与 build_wechat_expiry_alert 同口径）。"""
    if status is None:
        return None
    if not status.logged_in or status.is_expired:
        return "expired"
    if status.expire_time_ms:
        hours_left = (status.expire_time_ms / 1000 - now_ts) / 3600
        if hours_left <= 12:
            return "warn12"
        if hours_left <= warn_days * 24:
            return "warn24"
    return None


def build_wechat_expiry_alert(status, now_ts: float, warn_days: int) -> Optional[tuple[str, str, list]]:
    """微信登录状态 → (title, markdown, btns) 提醒，或 None（健康 / 服务连不上时不提醒）。

    btns 带「扫码续期」按钮直达看板登录页（开页即实时二维码、免 ssh）；text 附 ssh 兜底行。
    status=None 表示 wechat-api 连不上——那是另一类问题，不误报「登录过期」。
    """
    if status is None:
        return None
    btns = _wechat_login_btns()
    if not status.logged_in or status.is_expired:
        text = ("### ⚠️ 微信公众号登录已失效\n\n"
                "新品监测日报将**暂停附带行业文章**（其余情报照常）。\n\n"
                "**点下方「🔑 扫码续期」**打开看板登录页，用微信扫一扫即可恢复。\n\n"
                + _WECHAT_RELOGIN_FALLBACK)
        return "微信公众号登录已失效", text, btns
    if status.expire_time_ms:
        hours_left = (status.expire_time_ms / 1000 - now_ts) / 3600
        if hours_left <= warn_days * 24:
            text = (f"### ⏰ 微信公众号登录将在约 {max(0, round(hours_left))} 小时后过期\n\n"
                    f"账号：{status.nickname or '—'}。**点下方「🔑 扫码续期」**提前续期，避免日报断档。\n\n"
                    + _WECHAT_RELOGIN_FALLBACK)
            return "微信公众号登录即将过期", text, btns
    return None


# 提醒去重状态（内存，重启重置——至多多推一条，可接受）：同一登录态（expire_ms）下，
# 同档/更轻的档不重复推；续期后 expire_ms 变 → 重置，重新按档推。
_TIER_RANK = {"warn24": 1, "warn12": 2, "expired": 3}
_wechat_alert_state: dict = {"expire_ms": None, "tier": None}


async def alert_wechat_login_if_needed() -> bool:
    """检查 wechat 登录状态，失效/将过期（24h/12h 两档）则推钉钉带「扫码续期」按钮。
    未启用 / 未配 webhook → 不发。按档去重避免每次检查刷屏。钉死 maintainer 群、永不进领导群。"""
    if not (settings.WECHAT_ENABLED and dingtalk.is_enabled()):
        return False
    from app.services.wechat_articles import get_login_status
    status = await get_login_status()
    now = time.time()
    tier = _wechat_alert_tier(status, now, settings.WECHAT_EXPIRY_WARN_DAYS)
    if tier is None:
        return False
    cur_exp = status.expire_time_ms if status else None
    st = _wechat_alert_state
    if st["expire_ms"] != cur_exp:      # 新登录态（含续期后）→ 重置已推档
        st["expire_ms"], st["tier"] = cur_exp, None
    if st["tier"] is not None and _TIER_RANK[tier] <= _TIER_RANK[st["tier"]]:
        return False                    # 同档/更轻，已推过、不重复
    built = build_wechat_expiry_alert(status, now, settings.WECHAT_EXPIRY_WARN_DAYS)
    if not built:
        return False
    sent = await dingtalk.send_action_card(*built, target="maintainer")
    if sent:
        st["tier"] = tier
    return sent


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


def _radar_store_country(app) -> str:
    """iOS 商店链接的地区路径：优先 us，否则首个可见 storefront（软启动区，避免 us 路径 404）。
    GP 行 storefronts=='gp' → 无地区意义，_store_url 安卓分支忽略 country。"""
    sfs = [s for s in (app.storefronts or "").split(",") if s and s != "gp"]
    if "us" in sfs:
        return "us"
    return sfs[0] if sfs else "us"


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
