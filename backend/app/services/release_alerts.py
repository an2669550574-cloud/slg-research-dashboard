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
import time
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.publisher import PublisherEntity, PublisherItunesApp, PublisherItunesArtist
from app.services import dingtalk

logger = logging.getLogger(__name__)

_COMBO_FLAG = {"us": "🇺🇸", "jp": "🇯🇵", "kr": "🇰🇷", "cn": "🇨🇳", "tw": "🇹🇼", "de": "🇩🇪", "gb": "🇬🇧"}


def _fmt_money(v) -> str:
    return f"${v:,.0f}" if v else "—"


def _combo_label(country: str, platform: str) -> str:
    flag = _COMBO_FLAG.get(country.lower(), "")
    return f"{flag} {country.upper()} · {platform}".strip()


def _store_url(app_id: str, country: str, platform: str) -> Optional[str]:
    """榜单行 app_id → 商店页链接。iOS 数字 id 可直拼；其余形态（包名/安卓）拼不出。"""
    if platform == "ios" and str(app_id).isdigit():
        return f"https://apps.apple.com/{country.lower()}/app/id{app_id}"
    return None


# ── 每日情报汇总（竞品异动 + 两层新品，全 combo 一条） ─────────────────────

def build_movement_lines(s: dict) -> list[str]:
    """movement 摘要 → 人读行。与 Sentry 的 [NEW]/[UP] 机器码刻意分离。"""
    lines = []
    for e in s["new_entrants"]:
        frm = "榜外" if e["prev_rank"] is None else f"#{e['prev_rank']}"
        lines.append(f"🆕 **{e['name']}** 空降 **#{e['cur_rank']}**（{frm} →）")
    for e in s["surges"]:
        lines.append(f"📈 **{e['name']}** #{e['prev_rank']} → **#{e['cur_rank']}**（↑{e['prev_rank'] - e['cur_rank']}）")
    for e in s["drops"]:
        to = "榜外" if e["cur_rank"] is None else f"#{e['cur_rank']}"
        lines.append(f"📉 **{e['name']}** 跌出 Top 榜（#{e['prev_rank']} → {to}）")
    for e in s["revenue_spikes"]:
        lines.append(f"💰 **{e['name']}** 收入 **{e['pct']:+.0f}%**（{_fmt_money(e['prev_revenue'])} → {_fmt_money(e['cur_revenue'])}）")
    return lines


def _enrich_suffix(e: Optional[dict]) -> str:
    """新品行的富化尾巴：子品类 / 价格 / 上架日（log 表里有才拼，没有不占位）。"""
    if not e:
        return ""
    parts = [p for p in (
        e.get("genre"),
        e.get("price"),
        f"上架 {e['release_date']}" if e.get("release_date") else None,
    ) if p]
    return f" · {' · '.join(parts)}" if parts else ""


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


def _match_articles_to_apps(per_combo: list[dict], article_list: list) -> dict:
    """搜到的文章 → 按「标题/摘要含新品名」聚合到 app_id：{app_id: [WechatArticle]}。

    用 (c.get("market") or {}) 而非 c.get("market", {})——entry 的 market/publisher
    初始为 None，后者在 key 存在时返回 None 会 AttributeError（曾导致整段静默失效）。
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
            if nm in text:
                for aid in app_ids:
                    out.setdefault(aid, []).append(a)
    return out


def build_newcomer_lines(market: dict, publisher: dict,
                         enrich: Optional[dict] = None,
                         articles: Optional[dict] = None) -> list[str]:
    """两层新品检测 → 人读行。
    enrich: {app_id: {genre, price, release_date}}
    articles: {app_id: [WechatArticle]} 微信公众号文章
    """
    enrich = enrich or {}
    articles = articles or {}
    lines = []
    for n in (market.get("newcomers") or [])[:10]:
        tag = "" if n.get("is_slg") else " ⚠️ 新厂商待识别"
        base = f"✨ **{n['name']}** 空降 **#{n['rank']}** — {n.get('publisher') or '?'}"
        base += f"（{_fmt_money(n.get('revenue'))}）{_enrich_suffix(enrich.get(n.get('app_id')))}{tag}"
        base += _articles_suffix(articles.get(n.get('app_id')))
        lines.append(base)
    for n in (publisher.get("newcomers") or [])[:10]:
        rank = f"#{n['rank']}" if n.get("rank") else "进榜"
        base = f"🏢 **{n['entity_name']}** 新品 **{n['name']}** {rank}"
        base += _articles_suffix(articles.get(n.get('app_id')))
        lines.append(base)
    return lines


def build_daily_digest(per_combo: list[dict], today: str,
                       articles: Optional[dict] = None) -> Optional[tuple[str, str, list[tuple[str, str]]]]:
    """全 combo 检测结果 → (title, markdown, btns)。全空 → None（不发）。

    per_combo: [{country, platform, movement: dict|None, market: dict|None, publisher: dict|None}]
    articles: {app_id: [WechatArticle]} 微信公众号文章（按 app_id 聚合）
    """
    sections: list[str] = []
    btns: list[tuple[str, str]] = []
    total = 0
    for c in per_combo:
        lines: list[str] = []
        if c.get("movement"):
            lines += build_movement_lines(c["movement"])
        if c.get("market") or c.get("publisher"):
            lines += build_newcomer_lines(c.get("market") or {}, c.get("publisher") or {},
                                          enrich=c.get("enrich"), articles=articles)
        if not lines:
            continue
        total += len(lines)
        sections.append(f"**{_combo_label(c['country'], c['platform'])}**\n\n" + "\n\n".join(lines))
        # 按钮：每 combo 取头一条异动/新品的商店页（最多 5 个，按 combo 顺序）
        for e in ((c.get("movement") or {}).get("new_entrants") or [])[:1] + \
                 ((c.get("movement") or {}).get("surges") or [])[:1]:
            url = _store_url(e.get("app_id", ""), c["country"], c["platform"])
            if url and len(btns) < 5:
                btns.append((f"{e['name']} →", url))
    if not sections:
        return None
    head = f"### 📡 SLG 每日情报 · {today}（{total} 项）"
    return f"每日情报 {today}", "\n\n---\n\n".join([head] + sections), btns


async def send_daily_digest() -> bool:
    """日级 job 入口：对全部已配置 combo 重跑检测，拼一张卡发一次。

    只纳入当天有新快照的 combo：movement 靠 today_missing 闸门，新品靠
    as_of == today 闸门——次市场（周/月级同步）的旧快照不会被每天重报。
    """
    if not dingtalk.is_enabled():
        return False
    from app.services.movement import detect_movement
    from app.services.newcomers import detect_newcomers, detect_publisher_newcomers

    today = utcnow_naive().strftime("%Y-%m-%d")
    per_combo: list[dict] = []
    all_newcomer_names: set[str] = set()  # 收集所有新品名称，用于批量搜微信文章

    for country, platform in settings.sync_combos_list:
        entry: dict = {"country": country, "platform": platform, "movement": None,
                       "market": None, "publisher": None, "enrich": None}
        try:
            m = await detect_movement(country, platform, today)
            if not m.get("today_missing"):
                entry["movement"] = m
            market = await detect_newcomers(country, platform)
            publisher = await detect_publisher_newcomers(country, platform)
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

    msg = build_daily_digest(per_combo, today, articles=articles_by_app)
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
    return "Google Play" if (app.storefronts or "") == "gp" else "App Store"


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
