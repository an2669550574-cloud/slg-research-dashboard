"""tracked iOS games 版本变更追踪（需求② / ADR 0003）。

日级重查 tracked 竞品的 iTunes 版本号（零 ST，批量 lookup），与 Game.version 比对：
- 首次（Game.version 为 NULL）：填基线、**不算变更**（no_baseline，与新品检测同哲学，
  避免上线即把所有 app 当「刚更新」刷屏）。
- 版本变了：写一条 game_histories(event_type='version') 变更事件 + 更新 Game 当前值，
  并把变更收集返回——供每日 digest「版本更新」段读当天事件展示。

**版本号来源 = iOS 数字 trackId 批量 iTunes lookup**（一次 100 个、零 ST）。
trackId 取自 `Game.ios_track_id`（人工核对补的精确 iOS id）优先，否则 app_id 本身是
数字时用之。HK 现有 tracked games 多用 GP 包名作 app_id（iTunes 用包名查不到 iOS，
GP 包名 ≠ iOS bundleId），故靠 ios_track_id 补；没补 trackId 的 app 跳过、不追踪
（曾试 iTunes search by 游戏名兜底，但泛词名同名歧义大、iOS 名常带副标题——如
'Lords Mobile'/'Vikings' 按名搜会漏——故改走「人工补精确 trackId」，零误匹配。
注：'Warpath' 实为 Lilith 的 'Warpath: Ace Shooter'(id 1529067679, genre=Strategy)，
初稿曾被 Century(GP 包名)/Lilith(iOS 发行) 品牌混淆误判为「误匹配」，2026-06-27 已更正补入）。
Android 无版本源（GP 页 JSON-LD 无 version）→ 只查 platform='ios' 的 tracked games。
"""
import logging

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.game import Game
from app.models.history import GameHistory
from app.services import llm_gateway
from app.services.appstore import fetch_apps_bulk

logger = logging.getLogger(__name__)

_NOTES_PROMPT = """你是手游竞品调研助手。下面是某竞品游戏一次版本更新的更新说明（release notes）。\
用一句简体中文提炼**对玩家 / 竞品分析有意义的实质变化**（新赛季 / 新玩法 / 新英雄 / 新地图 / \
大型活动 / 付费点 / 平衡性调整等），不超过 30 字，不带书名号、不带引号。若更新说明只是 \
bug 修复 / 性能优化 / 常规维护这类无实质内容，只输出四个字：例行更新。只输出这一句话本身。

游戏：{name}
版本：{old} → {new}
更新说明：
{notes}"""


async def _summarize_notes(name: str, old_v: str, new_v: str, notes: str) -> str | None:
    """版本 release notes → 一句中文实质变化（新赛季 / 玩法 / 付费点…）。无 notes / 无 key /
    mock / 失败 / 纯 bugfix → None（digest 优雅降级为只显版本号）。走太石网关便宜文本模型，
    版本变更本就稀少故 cost 可忽略。"""
    if settings.USE_MOCK_DATA or not settings.TAISHI_API_KEY:
        return None
    notes = (notes or "").strip()
    if not notes:
        return None
    prompt = _NOTES_PROMPT.format(name=name or "", old=old_v, new=new_v, notes=notes[:1200])
    try:
        client = llm_gateway.get_client()
        # max_tokens 必须给思考型模型留内部 reasoning 余量：网关 gemini-*-preview 的
        # reasoning token 计入 max_tokens，120 会被烧光 → finish_reason='length' +
        # content=''（prod 实测 2026-07-03，📝 自上线起从未渲染过的根因）。答案本身
        # 只要一句话，1024 足够思考 + 输出。
        resp = await client.chat.completions.create(
            model=settings.TAISHI_TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024, temperature=0.2,
        )
        content = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        if not content:
            # 空内容不再静默吞掉（曾把 max_tokens 饿死伪装成「无摘要」数月不可见）。
            finish = resp.choices[0].finish_reason if resp.choices else "no-choices"
            logger.warning("version notes summarize empty for %s (finish_reason=%s)",
                           name, finish)
            return None
    except Exception:
        logger.warning("version notes summarize failed for %s", name, exc_info=True)
        return None
    # 单行化 + 去引号/书名号包裹；纯 bugfix（LLM 返回「例行更新」）→ None 不渲染子行。
    content = content.replace("\n", " ").strip().strip('"“”「」').strip()
    if not content or content == "例行更新":
        return None
    return content[:60]


def _numeric(app_id: str) -> str:
    """app_id 归一成纯数字（去掉可能的 'id' 前缀）。"""
    return (app_id or "").replace("id", "").strip()


def _track_id(g: Game) -> str | None:
    """该 game 的 iOS 数字 trackId：优先 ios_track_id（人工补的精确 id），否则
    app_id 本身是数字时用之。GP 包名且无 ios_track_id → None（不追踪，待补 trackId）。"""
    if g.ios_track_id and str(g.ios_track_id).strip().isdigit():
        return str(g.ios_track_id).strip()
    nid = _numeric(g.app_id)
    return nid if nid.isdigit() else None


async def check_tracked_versions() -> list[dict]:
    """重查 tracked iOS games 版本，检测变更并落库。返回变更列表（供 digest）。

    每条变更 = {app_id, name, old, new, date}。USE_MOCK_DATA 下整体 no-op。
    """
    if settings.USE_MOCK_DATA:
        return []
    changes: list[dict] = []
    async with AsyncSessionLocal() as db:
        games = (await db.execute(
            select(Game).where(Game.platform == "ios"))).scalars().all()
        # trackId → Game；没有可用 trackId 的（GP 包名未补）跳过。
        tid_map: dict[str, Game] = {}
        for g in games:
            tid = _track_id(g)
            if tid:
                tid_map[tid] = g
        if not tid_map:
            return []
        bulk = await fetch_apps_bulk(list(tid_map), country="us")
        today = utcnow_naive().strftime("%Y-%m-%d")
        for tid, g in tid_map.items():
            cur = bulk.get(tid)
            if not cur or not cur.get("version"):
                continue  # 查不到 / 无版本号 → 不动
            new_v = cur["version"]
            new_d = cur.get("current_version_date") or today
            if g.version is None:
                g.version, g.version_date = new_v, new_d  # 基线，不算变更
                continue
            if new_v != g.version:
                old_v = g.version
                notes = cur.get("release_notes") or ""
                db.add(GameHistory(
                    app_id=g.app_id, event_date=new_d, event_type="version",
                    title=f"版本更新 {old_v} → {new_v}",
                    description=notes[:1000] or None,
                    source="appstore"))
                g.version, g.version_date = new_v, new_d
                changes.append({"app_id": g.app_id, "name": g.name, "old": old_v,
                                "new": new_v, "date": new_d, "_notes": notes})
        await db.commit()
    # LLM 一句话提炼更新实质（放 DB 会话外，不占连接等 LLM）。变更稀少故每条一次调用
    # 可接受；无 notes / 无 key / mock / 纯 bugfix → notes_cn=None，digest 只显版本号。
    for c in changes:
        c["notes_cn"] = await _summarize_notes(c["name"], c["old"], c["new"], c.pop("_notes", ""))
    if changes:
        logger.info("version tracker: %d change(s): %s",
                    len(changes), [c["app_id"] for c in changes])
    return changes
