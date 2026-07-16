"""白名单卫生自检：pin / alias 建档判断 × LLM 玩法分类的交叉审计（零 ST、零新增 LLM）。

背景（2026-07-16）：一周内两起「人工建档判断与产品现实脱节」事故都靠人眼回扫领导卡
才发现——① CyberJoy 降级 is_slg 时漏删 Galaxy Defense 塔防 pin，漏网 10 天；
② OpenMind World（一半乙女游戏的多品类模板小厂）整账号接入雷达。而 #242 之后系统里
躺着现成的交叉审计信号：影子行/回补管道的 LLM 玩法子品类。本模块把「下一个 CyberJoy」
的发现从事故驱动（回扫撞见）变成自检驱动（周察卡 ⚠️ 段，≤7 天必现）。

两条审计（都只产出「人工复核」提示，绝不自动改档）：
- **pin 矛盾**：钉选 app 的 LLM 分类落在明确非 SLG 子集（塔防/放置/卡牌/休闲/城建/其他）
  ——pin 语义=「单品即 SLG」，与分类直接矛盾。三消合成不算证据（P&S 类混合品）。
- **主体疑错标**：挂 alias 且 is_slg=1 的主体，其近窗口**上榜**产品中已分类的 ≥1 个、
  全部落在明确非 SLG 子集、且无任何 SLG 分类——alias 会把该主体名下所有产品判 SLG，
  若其上榜面全非 SLG，大概率整个主体错标（CyberJoy 案的形状）。

误报边界（刻意保守）：未分类不算证据；混合品类（三消合成）不算证据；主体级要求
「有分类证据且零 SLG 反证」。审计行带分类明细，人工一眼可判。
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select

from app.database import AsyncSessionLocal, utcnow_naive

logger = logging.getLogger(__name__)

# 主体级审计回看的上榜窗口（天）：太短抓不到低频次市场，太长会翻出已下榜的陈年产品。
AUDIT_RANKINGS_WINDOW_DAYS = 90


async def audit_whitelist_hygiene() -> dict:
    """返回 {"pin_conflicts": [...], "entity_suspects": [...]}；无发现 → 两个空列表。

    pin_conflicts: [{app_id, app_name, entity_name, subgenre}]
    entity_suspects: [{entity_name, evidence: [{app_name, subgenre}, ...]}]
    """
    from app.models.game import GameRanking
    from app.models.publisher import PublisherAppId, PublisherEntity
    from app.services.newcomer_i18n import AUDIT_CLEAR_NON_SLG, SLG_CORE_SUBGENRES
    from app.services.newcomers import _kw_hit, _load_entity_matchers, _tokens
    from app.services.release_alerts import _subgenres_for_apps

    out: dict = {"pin_conflicts": [], "entity_suspects": []}

    # ── 审计① pin ↔ 分类矛盾 ────────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        pins = (await db.execute(
            select(PublisherAppId.app_id, PublisherEntity.name)
            .join(PublisherEntity, PublisherEntity.id == PublisherAppId.entity_id)
        )).all()
    pin_ids = {app_id for app_id, _ in pins}
    sg_pins = await _subgenres_for_apps(pin_ids)
    conflict_ids = [(app_id, ename) for app_id, ename in pins
                    if sg_pins.get(app_id) in AUDIT_CLEAR_NON_SLG]
    if conflict_ids:
        names = await _app_display_names({a for a, _ in conflict_ids})
        out["pin_conflicts"] = [
            {"app_id": app_id, "app_name": names.get(app_id) or app_id,
             "entity_name": ename, "subgenre": sg_pins[app_id]}
            for app_id, ename in conflict_ids
        ]

    # ── 审计② alias 主体疑错标（仅看 alias 归属的上榜面；pin 归属由审计① 逐 app 管）──
    matchers = [m for m in await _load_entity_matchers()
                if m["is_slg"] and m["kw_tokens"]]
    if matchers:
        cutoff = (utcnow_naive() - timedelta(days=AUDIT_RANKINGS_WINDOW_DAYS)).strftime("%Y-%m-%d")
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(GameRanking.app_id, GameRanking.publisher, GameRanking.name)
                .where(GameRanking.date >= cutoff, GameRanking.publisher.is_not(None))
                .distinct()
            )).all()
            # tracked 竞品 = 人工确认过的 SLG（games 表）——比 LLM 分类更硬的反证。
            # 首跑实锤的误报形状（2026-07-16）：点点互动旗下 Whiteout Survival（tracked
            # 真 SLG）没有分类行、唯一被分类的恰是城建 Frozen City → 证据面残缺冤枉主体。
            from app.models.game import Game
            tracked_ids = set((await db.execute(select(Game.app_id))).scalars().all())
        apps_by_entity: dict[str, dict[str, str]] = {}  # entity_name -> {app_id: app_name}
        pins_by_entity: dict[str, set[str]] = {}
        for app_id, publisher, name in rows:
            pub_tokens = _tokens(publisher)
            if not pub_tokens:
                continue
            for m in matchers:
                if any(_kw_hit(pub_tokens, kw) for kw in m["kw_tokens"]):
                    apps_by_entity.setdefault(m["entity_name"], {})[app_id] = name or app_id
                    pins_by_entity.setdefault(m["entity_name"], m["app_ids"])
                    break
        all_ids = {a for apps in apps_by_entity.values() for a in apps}
        sg_all = await _subgenres_for_apps(all_ids)
        for ename, apps in apps_by_entity.items():
            # SLG 反证（任一即豁免）：上榜面含 tracked 竞品 / 含主体自己的 pin（单品即
            # SLG，人工钉的）/ 含 LLM 分类为 SLG 的产品。
            if any(a in tracked_ids for a in apps):
                continue
            if apps.keys() & (pins_by_entity.get(ename) or set()):
                continue
            classified = {a: sg_all[a] for a in apps if a in sg_all}
            if not classified:
                continue  # 无分类证据不指控
            if any(sg in SLG_CORE_SUBGENRES for sg in classified.values()):
                continue
            clear = {a: sg for a, sg in classified.items() if sg in AUDIT_CLEAR_NON_SLG}
            if not clear or len(clear) != len(classified):
                continue  # 混有三消合成等模糊分类 → 证据不干净，不指控
            out["entity_suspects"].append({
                "entity_name": ename,
                "evidence": [{"app_name": apps[a], "subgenre": sg}
                             for a, sg in sorted(clear.items())],
            })

    return out


async def _app_display_names(app_ids: set[str]) -> dict[str, str]:
    """pin 只存 app_id，展示名 best-effort：newcomer 台账优先、rankings 兜底。"""
    from app.models.game import GameRanking
    from app.models.newcomer import MarketNewcomerLog

    out: dict[str, str] = {}
    if not app_ids:
        return out
    ids = list(app_ids)
    async with AsyncSessionLocal() as db:
        for aid, nm in (await db.execute(
            select(MarketNewcomerLog.app_id, MarketNewcomerLog.name)
            .where(MarketNewcomerLog.app_id.in_(ids))
        )).all():
            if nm:
                out.setdefault(aid, nm)
        missing = [a for a in ids if a not in out]
        if missing:
            for aid, nm in (await db.execute(
                select(GameRanking.app_id, GameRanking.name)
                .where(GameRanking.app_id.in_(missing)).distinct()
            )).all():
                if nm:
                    out.setdefault(aid, nm)
    return out


def build_audit_lines(findings: dict) -> list[str]:
    """审计发现 → 周察卡「🧭 白名单卫生自检」段 markdown 行；无发现 → []。"""
    from app.services.release_alerts import _md_name

    lines: list[str] = []
    for c in findings.get("pin_conflicts") or []:
        lines.append(f"- ⚠️ pin 矛盾：**{_md_name(c['app_name'])}**"
                     f"（{_md_name(c['entity_name'])}）LLM 分类=**{c['subgenre']}**"
                     f" → 复核该 pin（`app_id={c['app_id']}`）")
    for s in findings.get("entity_suspects") or []:
        ev = "、".join(f"{_md_name(e['app_name'], maxlen=18)}({e['subgenre']})"
                       for e in s["evidence"][:4])
        more = f" 等 {len(s['evidence'])} 款" if len(s["evidence"]) > 4 else ""
        lines.append(f"- ⚠️ 主体疑错标：**{_md_name(s['entity_name'])}**（alias 在册、"
                     f"is_slg=1）近 {AUDIT_RANKINGS_WINDOW_DAYS} 天上榜产品已分类的全部"
                     f"非 SLG：{ev}{more} → 复核 is_slg / 改 pin 制")
    return lines
