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

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.game import Game
from app.models.newcomer import MarketNewcomerLog
from app.models.publisher import PublisherAppId, PublisherItunesApp
# 下列名字 import 进本模块命名空间 → 测试可 monkeypatch dt.<name> 拦截外网。
from app.services.newcomers import _load_ignore_keys
from app.services.newcomer_log import enrich_fields
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
