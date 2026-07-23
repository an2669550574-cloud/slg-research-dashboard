"""发现层 · 人工线报快速分诊（ADR 0006 切片3 的第一步，只读）。

贴一个 **GP 包名 / iOS 数字 app_id / 商店 URL** → 归一成 (app_id, platform) → 本地覆盖核查
（tracked / detected / ignored / unknown，零外网）→ **仅 unknown 才**零 ST 溯源（免费富化 +
反解开发者账号 + LLM 子品类）→ 返回**建档草稿**供人工确认后走 `POST /api/publishers/`。

为什么要这个：未追踪主体的全新壳（如 Eastlume《Last Duo》= 仅 GP、5K 软启动）三层自动监测
（榜面检出 / RSS 早鸟 / 商店雷达）结构性全漏——雷达只能 diff **已知**开发者账号。这类长尾唯一
入口是人给的线报（公众号等）。本工具把「人贴锚点 → 机器 30 秒完成覆盖核查 + 零 ST 溯源 + 建档
草稿」固化，绕开缺失的「名→id 自动搜索」桥（那是更脆的后续 feature）。

**零 ST 硬约束**：本模块只准调 `enrich_fields` / `resolve_*` / `classify_subgenre`（全免费源），
**禁止 import 任何 sensor_tower**（CI grep 断言，见 test_discovery_triage）。切片1**只读**：不落库、
不改 digest、不挂调度——先验证分诊精度，落库出口与维护者卡段留后续切片。
"""
import re
from typing import Optional
from urllib.parse import quote_plus

import httpx
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.game import Game
from app.models.newcomer import MarketNewcomerLog
from app.models.publisher import PublisherAppId, PublisherItunesApp
# 下列名字 import 进本模块命名空间 → 测试可 monkeypatch dt.<name> 拦截外网。
from app.services.newcomers import _load_ignore_keys
from app.services.newcomer_log import enrich_fields, record_discovery_newcomers
from app.services.newcomer_i18n import classify_subgenre, SLG_CORE_SUBGENRES
from app.services.gp_releases import resolve_gp_developer_for_package
from app.services.itunes_releases import resolve_artist_for_app

# GP 包名：字母开头、至少一个点分段（com.foo.bar）。iOS：≥6 位纯数字 track id。
_GP_PKG_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+")
_IOS_URL_RE = re.compile(r"/id(\d+)")
_GP_ID_RE = re.compile(r"[?&]id=([a-zA-Z][\w.]+)")


def normalize_tip(tip: str) -> Optional[dict]:
    """线报字符串 → {"app_id", "platform"}。识别 App Store URL(/idN)、GP URL(?id=pkg)、
    裸 GP 包名、裸 iOS 数字 id。认不出 → None。"""
    t = (tip or "").strip()
    if not t:
        return None
    # App Store 链接：.../idNNNNN
    m = _IOS_URL_RE.search(t)
    if m:
        return {"app_id": m.group(1), "platform": "ios"}
    # Google Play 链接：...?id=com.foo.bar
    if "play.google.com" in t:
        m = _GP_ID_RE.search(t)
        if m and "." in m.group(1):
            return {"app_id": m.group(1), "platform": "android"}
        return None  # 是 GP 链接但抽不出包名 → 别误落到裸包名分支
    # 明确是别的 URL 但没被上面认出 → 不猜
    if t.startswith("http") or "apple.com" in t:
        return None
    # 裸 GP 包名（整串就是一个包名）
    if _GP_PKG_RE.fullmatch(t):
        return {"app_id": t, "platform": "android"}
    # 裸 iOS 数字 id
    if t.isdigit() and len(t) >= 6:
        return {"app_id": t, "platform": "ios"}
    return None


async def _coverage(app_id: str) -> str:
    """本地 4 表覆盖核查（零外网）：ignored > tracked > detected > unknown。
    tracked = 已追踪竞品(games) / 已 pin(publisher_app_ids) / 雷达账号下 app(publisher_itunes_apps)。
    detected = 已被检出(market_newcomer_log)。"""
    _pub_keys, ignore_app_ids = await _load_ignore_keys()
    if app_id in ignore_app_ids:
        return "ignored"
    async with AsyncSessionLocal() as db:
        if (await db.execute(select(Game.id).where(
                (Game.app_id == app_id) | (Game.ios_track_id == app_id)).limit(1))).first():
            return "tracked"
        if (await db.execute(select(PublisherAppId.id).where(
                PublisherAppId.app_id == app_id).limit(1))).first():
            return "tracked"
        if (await db.execute(select(PublisherItunesApp.id).where(
                PublisherItunesApp.track_id == app_id).limit(1))).first():
            return "tracked"
        if (await db.execute(select(MarketNewcomerLog.id).where(
                MarketNewcomerLog.app_id == app_id).limit(1))).first():
            return "detected"
    return "unknown"


_GP_SEARCH_PKG_RE = re.compile(r"/store/apps/details\?id=([a-zA-Z0-9._]+)")


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]+", "", (s or "").lower())


def _name_match(a: str, b: str) -> bool:
    """两名是否够像（防搜索首结果张冠李戴）：规范化后互为子串，或前 6 字符命中。"""
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return False
    return na in nb or nb in na or (len(na) >= 4 and na[:6] in nb)


async def _resolve_gp_by_name(name: str) -> Optional[dict]:
    from app.services.gp_releases import _get_html, app_page_url, parse_app_detail
    url = f"https://play.google.com/store/search?q={quote_plus(name)}&c=apps&hl=en&gl=US"
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            pkgs = _GP_SEARCH_PKG_RE.findall(await _get_html(client, url))
            if not pkgs:
                return None
            pkg = pkgs[0]   # 首结果（独特名准、泛名可能错，靠 match 标记提示）
            detail = parse_app_detail(await _get_html(client, app_page_url(pkg)), pkg)
    except Exception:
        return None
    store_name = detail.get("trackName") or pkg
    return {"app_id": pkg, "platform": "android", "store_name": store_name,
            "store_url": app_page_url(pkg), "match": _name_match(name, store_name)}


async def _resolve_itunes_by_name(name: str) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get("https://itunes.apple.com/search",
                                 params={"term": name, "entity": "software", "limit": 3, "country": "us"})
            results = r.json().get("results") or []
    except Exception:
        return None
    if not results:
        return None
    top = results[0]
    return {"app_id": str(top.get("trackId")), "platform": "ios",
            "store_name": top.get("trackName"), "store_url": top.get("trackViewUrl"),
            "publisher": top.get("artistName"), "match": _name_match(name, top.get("trackName") or "")}


async def resolve_name_to_store(name: str, platform_hint: Optional[str] = None) -> Optional[dict]:
    """游戏名 → 商店 app_id（零 ST）。安卓/未知先 GP 搜索首结果、再 iTunes；iOS 反之。优先返回
    名字对得上的（match=True）；都对不上返回 best-effort 首结果（match=False，供人工判）；全空返 None。
    首结果启发式，故本函数只服务「出候选供人工核」，不做无人值守自动落库。"""
    fns = [_resolve_gp_by_name, _resolve_itunes_by_name]
    if platform_hint == "ios":
        fns = [_resolve_itunes_by_name, _resolve_gp_by_name]
    best = None
    for fn in fns:
        res = await fn(name)
        if res and res.get("app_id"):
            if res.get("match"):
                return res
            best = best or res
    return best


async def triage(tip: str, dry_run: bool = True) -> dict:
    """线报 → 分诊结果 + 建档草稿。切片1 恒只读（dry_run 仅为 API 前向兼容，落库出口见后续切片）。"""
    parsed = normalize_tip(tip)
    if not parsed:
        return {"tip": tip, "recognized": False,
                "hint": "认不出——请给 GP 包名(com.x.y) / iOS 数字 id / App Store 或 GP 链接"}
    app_id, platform = parsed["app_id"], parsed["platform"]
    out = {"tip": tip, "recognized": True, "app_id": app_id, "platform": platform,
           "dry_run": True, "coverage": await _coverage(app_id)}
    if out["coverage"] != "unknown":
        return out  # 已覆盖：短路，不出外网

    # ── unknown → 零 ST 溯源 ──
    enrich = await enrich_fields(app_id, "us", platform)
    if platform == "android":
        dev = await resolve_gp_developer_for_package(app_id)
    else:
        dev = await resolve_artist_for_app(app_id)
    dev = dev or {}
    name = dev.get("app_name") or (enrich or {}).get("name")
    genre = (enrich or {}).get("genre")
    description = (enrich or {}).get("description")
    subgenre = await classify_subgenre(name, genre, description)
    is_slg_core = subgenre in SLG_CORE_SUBGENRES if subgenre else None

    radar_account = None
    if dev.get("artist_id"):
        radar_account = {"artist_id": dev["artist_id"],
                         "platform": "gp" if platform == "android" else "ios"}
    out.update({
        "enrich": enrich,
        "developer_account": dev or None,       # {artist_id, artist_name, app_name}，可一键挂雷达
        "subgenre_cn": subgenre,
        "is_slg_core": is_slg_core,
        # 建档草稿（不落库）：人工核实后走 POST /api/publishers/ + POST /{id}/itunes-artists。
        "draft_entity": {
            "name": dev.get("artist_name") or "(待确认厂商名)",
            "is_slg": bool(is_slg_core),         # 草稿建议值，人工确认
            "pin": {"app_id": app_id, "platform": platform},
            "radar_account": radar_account,      # 挂上后该开发者账号后续新品自动进雷达
            "brief_stub": (
                f"线报分诊建档草稿：{name or app_id}（{platform}）· 子品类={subgenre or '未分类'}"
                f" · 开发者账号={dev.get('artist_name') or dev.get('artist_id') or '未反解'}。"
                f"零 ST 免费源富化。人工核实厂商归属/is_slg 后建档。"),
        },
        "note": ("未追踪 → 已零 ST 溯源出建档草稿。挂 radar_account 后该开发者账号后续新品自动被"
                 "雷达 diff。子品类未分类(mock/无描述/LLM 未给)时 is_slg 草稿为 False，需人工判。"),
    })
    return out


async def log_tip(tip: str) -> dict:
    """出口 B（期2）：人工确认线报 → 写 `chart_type='discovery'` 影子行。仅对**未追踪(unknown)**
    线报有意义；已覆盖的短路返回不落库。is_slg=True（人工确认此为值得盯的 SLG 新品线索）——
    一夜 drain 出中文摘要/子品类后，次日进维护者卡【📮 发现层线报】段。幂等（同 app 重复确认不重写）。"""
    res = await triage(tip)
    if not res.get("recognized"):
        return {**res, "logged": False, "reason": "认不出线报，无法落库"}
    if res.get("coverage") != "unknown":
        return {**res, "logged": False,
                "reason": f"coverage={res.get('coverage')}（非未追踪，无需落发现层）"}
    enrich = res.get("enrich") or {}
    dev = res.get("developer_account") or {}
    row = {
        "app_id": res["app_id"], "platform": res["platform"], "country": "WW",
        "name": dev.get("app_name") or enrich.get("name") or res["app_id"],
        "publisher": dev.get("artist_name"),
        "genre": enrich.get("genre"), "description": enrich.get("description"),
        "store_url": enrich.get("store_url"), "rating": enrich.get("rating"),
        "release_date": enrich.get("release_date"),
        "subgenre_cn": res.get("subgenre_cn"), "is_slg": True,
    }
    written = await record_discovery_newcomers([row])
    return {**res, "logged": bool(written), "written": written,
            "note": ("已写 discovery 影子行" if written else "该 app 已有 discovery 影子行（幂等跳过）")
                    + "；一夜 drain（翻译/子品类/视频）后次日进维护者卡【📮 发现层线报】段。"}


async def build_entity_from_tip(tip: str, name: Optional[str] = None,
                                is_slg: Optional[bool] = None,
                                hq_region: Optional[str] = None,
                                brief: Optional[str] = None) -> dict:
    """出口 A（期2.5）：人工确认线报 → 一键建 `PublisherEntity` + pin app_id + 挂开发者账号雷达。
    建号后该开发者账号后续新品**自动进雷达 diff**（这正是手工给 Eastlume 做的那套）。仅对未追踪线报
    建号（已覆盖的不重复建）。name/is_slg 可覆盖草稿；反解不出厂商名且未提供 name → 拒绝（要人给名）。
    名称型开发者 id > 30 字符（`artist_id` 列限）→ 跳过雷达挂接、其余照建并提示。"""
    from app.models.publisher import PublisherEntity, PublisherAppId, PublisherItunesArtist
    from app.services.slg_publishers import load_index_from_db

    res = await triage(tip)
    if not res.get("recognized"):
        return {**res, "built": False, "reason": "认不出线报，无法建号"}
    if res.get("coverage") != "unknown":
        return {**res, "built": False,
                "reason": f"coverage={res.get('coverage')}（已覆盖，勿重复建号）"}
    draft = res.get("draft_entity") or {}
    ent_name = name or (draft.get("name") if draft.get("name") != "(待确认厂商名)" else None)
    if not ent_name:
        return {**res, "built": False,
                "reason": "反解不出厂商名——请显式传 name 再建号（防建空壳档）"}
    ent_is_slg = is_slg if is_slg is not None else bool(draft.get("is_slg"))
    radar = draft.get("radar_account") or {}
    radar_skipped = None
    async with AsyncSessionLocal() as db:
        e = PublisherEntity(name=ent_name, hq_region=hq_region, is_slg=ent_is_slg,
                            brief=brief or draft.get("brief_stub"), sort_order=0)
        db.add(e)
        await db.flush()
        db.add(PublisherAppId(entity_id=e.id, app_id=res["app_id"],
                              note="发现层分诊建号 pin"))
        if radar.get("artist_id"):
            if len(str(radar["artist_id"])) <= 30:
                db.add(PublisherItunesArtist(entity_id=e.id, artist_id=radar["artist_id"],
                                             platform=radar.get("platform", "gp"),
                                             label=ent_name))
            else:
                radar_skipped = f"开发者 id 超 30 字符列限（{radar['artist_id']}）——雷达未挂，需数字型 id"
        await db.commit()
        eid = e.id
    await load_index_from_db()   # 刷新 is_slg 内存索引，pin 立即生效
    return {**res, "built": True, "entity_id": eid, "entity_name": ent_name,
            "is_slg": ent_is_slg, "radar_attached": bool(radar.get("artist_id")) and radar_skipped is None,
            "radar_skipped": radar_skipped,
            "note": "已建 entity + pin"
                    + ("+ 挂 GP/iOS 雷达账号（后续新品自动 diff）" if radar_skipped is None and radar.get("artist_id") else "")
                    + f"。回滚=DELETE /api/publishers/{eid}。"}
