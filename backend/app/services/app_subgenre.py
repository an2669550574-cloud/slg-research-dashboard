"""存量竞品玩法子品类回补（app_subgenre 表，P1-2）。

`market_newcomer_log.subgenre_cn` 只在新品被翻译时产出（`translate_pending_newcomers`），
**结构性覆盖不到**：① 从未作为新品检出的 established 竞品（movement 老熟人——领导每天看到的
榜单异动行，恰恰这些从不命中 ⚔️ 同赛道）② subgenre 特性上线前的老检出行（`summary_cn` 已写
但 `subgenre_cn=NULL`，不会被重译）。这里给「有描述的 is_slg 存量 app」补分类，写 app_subgenre
表，digest 建 `own_matches` 时作 fallback → 同赛道 ⚔️ 对老竞品也生效。

零 ST；LLM 走太石网关便宜文本模型（只分类不翻译，省 token），每日封顶 drain。
USE_MOCK_DATA / 无 key → no-op。前进式累积：每轮 drain 封顶，几天把存量分类完、稳态近 0。
"""
import logging

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def classify_pending_app_subgenres(cap: int | None = None) -> int:
    """给尚无子品类的 is_slg 存量 app 分类，写 app_subgenre。返回分类的 app 数。

    候选描述来源两路：① tracked `games`（Game.description，tracked 都是 SLG 竞品，全纳入）
    ② `market_newcomer_log`（有描述、subgenre_cn 缺的，按 live is_slg 门控）。**排除**已在
    app_subgenre（写行即已尝试）或 market_newcomer_log 已有 subgenre 的 app。写行即「已尝试」
    （subgenre 可能 None=词表外，不再重复烧），app_id 唯一保证幂等。
    """
    if settings.USE_MOCK_DATA or not settings.TAISHI_API_KEY:
        return 0
    lim = cap if cap is not None else settings.APP_SUBGENRE_BACKFILL_CAP
    if lim <= 0:
        return 0

    from app.models.game import Game
    from app.models.newcomer import MarketNewcomerLog, AppSubgenre
    from app.services.slg_publishers import is_slg

    async with AsyncSessionLocal() as db:
        classified = set((await db.execute(select(AppSubgenre.app_id))).scalars().all())
        has_sg = set((await db.execute(
            select(MarketNewcomerLog.app_id).where(MarketNewcomerLog.subgenre_cn.is_not(None))
        )).scalars().all())
        games = (await db.execute(
            select(Game.app_id, Game.name, Game.category, Game.description, Game.publisher)
            .where(Game.description.is_not(None), Game.description != "")
        )).all()
        logs = (await db.execute(
            select(MarketNewcomerLog.app_id, MarketNewcomerLog.name, MarketNewcomerLog.genre,
                   MarketNewcomerLog.description, MarketNewcomerLog.publisher)
            .where(MarketNewcomerLog.description.is_not(None),
                   MarketNewcomerLog.subgenre_cn.is_(None))
            .order_by(MarketNewcomerLog.first_detected_at.desc())
        )).all()
        # log 存档 is_slg（app_id 聚合）：live 判定对本地化 publisher 串会 miss（跨 combo
        # 分裂），任一行存过 1 就该进候选——否则误标行永不分类，视频救回/⚔️ 连带失效。
        archived_slg = set((await db.execute(
            select(MarketNewcomerLog.app_id).where(
                MarketNewcomerLog.is_slg.is_(True)).distinct())).scalars().all())

    skip = classified | has_sg
    # 候选池（app_id 去重）。tracked games 全纳入（都是主动录入的 SLG 竞品，不再 is_slg 门控——
    # 有的 tracked 竞品 publisher 串命不中 alias，但它就是要追的 SLG）；log 侧按
    # live is_slg OR 存档聚合 门控。
    cands: dict[str, tuple] = {}
    for app_id, name, cat, desc, _pub in games:
        if app_id in skip:
            continue
        cands.setdefault(app_id, (name, cat, desc, "game"))
    for app_id, name, genre, desc, pub in logs:
        if app_id in skip or app_id in cands:
            continue
        if not (is_slg(app_id, pub) or app_id in archived_slg):
            continue
        cands.setdefault(app_id, (name, genre, desc, "newcomer_log"))
    if not cands:
        return 0

    from app.services.newcomer_i18n import classify_subgenre
    done = 0
    for app_id, (name, genre, desc, source) in list(cands.items())[:lim]:
        # 子品类 + 游戏名中译同一次 LLM 调用产出（零增量成本）——存量竞品（movement 老熟人）
        # 不走新品中文化管道，这里是它们拿到中文名的唯一路径。name_cn 可为 None（LLM 按口径
        # 拿不准就留空），渲染层回落原名。
        sg = await classify_subgenre(name, genre, desc)
        # 中文名以商店一手数据为准（同 newcomer_i18n）：LLM 对无中文区发行的游戏会自造译名，
        # 且可能撞上另一款知名游戏的别名。商店查不到 → 留 None，渲染层保留原名。零 ST。
        from app.services.store_cn_name import fetch_store_cn_name
        name_cn = await fetch_store_cn_name(app_id)
        async with AsyncSessionLocal() as db:
            # 竞态防御：并发/重跑时 app_id 唯一约束兜底，先查后插。
            exists = (await db.execute(
                select(AppSubgenre.id).where(AppSubgenre.app_id == app_id))).scalar_one_or_none()
            if exists is None:
                db.add(AppSubgenre(app_id=app_id, name=name, subgenre_cn=sg,
                                   name_cn=name_cn, source=source))
                await db.commit()
        done += 1
    if done:
        logger.info("app subgenre backfill: %d app(s) classified", done)
    return done


# ── 人工判定覆盖层（source='manual'）────────────────────────────────────────

MANUAL_SOURCE = "manual"


async def set_manual_subgenre(app_id: str, subgenre_cn: str | None,
                              name: str | None = None) -> None:
    """把人工判定的子品类写进 app_subgenre 并标 source='manual'（覆盖 LLM 判定）。

    深度溯源得出的结论此前撑不过下一次同 app 新检出：LLM 分类挂在 market_newcomer_log 的
    **行**上，而 `translate_pending_newcomers` 对新检出行（summary_cn 为空）会重跑一次分类、
    并按 app_id 回写该 app 的**全部行**——2026-07-20 实测把前一天人工改好的 Battle Kiss
    子品类冲掉了。人工结论因此必须存在一个 LLM 不会去动的地方。

    落在 app_subgenre 而非新加字段，有三个好处：零迁移（source 列本就有）、天然 app 级
    （一次覆盖该 app 所有榜行）、且 `classify_pending_app_subgenres` 把「已在本表的 app」
    整体排除在候选外，人工行不会被回补 drain 重新分类。读取侧优先级见 `resolve_subgenres`。

    subgenre_cn 传 None = 人工判定「无合适子品类」，同样记为 manual（不再被 LLM 填）。
    """
    from app.models.newcomer import AppSubgenre
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(AppSubgenre).where(AppSubgenre.app_id == app_id))).scalar_one_or_none()
        if row is None:
            db.add(AppSubgenre(app_id=app_id, name=name, subgenre_cn=subgenre_cn,
                               source=MANUAL_SOURCE))
        else:
            row.subgenre_cn = subgenre_cn
            row.source = MANUAL_SOURCE
            if name:
                row.name = name
        await db.commit()


async def resolve_subgenres(app_ids) -> dict[str, str]:
    """app_id → 玩法子品类，**三级优先**：人工 > 榜行 LLM > 存量回补 LLM。

    子品类的**唯一读取口径**——digest 的「同赛道」匹配、下载榜线索的玩法门控都走这里。
    此前两处各写各的（一处 log 优先 + app_subgenre fallback，一处直接读 app_subgenre 全表），
    口径一分叉就会出现「这边判 SLG、那边判非 SLG」的鬼故事，故收口到一个函数。

    人工层置顶是这个函数存在的主要理由：LLM 会在每次同 app 新检出时重判并覆盖榜行，
    只有 source='manual' 的行它碰不到（见 set_manual_subgenre）。
    """
    from app.models.newcomer import MarketNewcomerLog, AppSubgenre
    out: dict[str, str] = {}
    ids = list(app_ids or [])
    if not ids:
        return out
    async with AsyncSessionLocal() as db:
        # ① 人工判定：最高优先级，LLM 不会覆盖
        for aid, sg in (await db.execute(
            select(AppSubgenre.app_id, AppSubgenre.subgenre_cn)
            .where(AppSubgenre.app_id.in_(ids),
                   AppSubgenre.source == MANUAL_SOURCE,
                   AppSubgenre.subgenre_cn.is_not(None))
        )).all():
            out[aid] = sg
        # ② 榜行 LLM 分类（新品/曾建档竞品）
        rest = [a for a in ids if a not in out]
        if rest:
            for aid, sg in (await db.execute(
                select(MarketNewcomerLog.app_id, MarketNewcomerLog.subgenre_cn)
                .where(MarketNewcomerLog.app_id.in_(rest),
                       MarketNewcomerLog.subgenre_cn.is_not(None))
            )).all():
                out.setdefault(aid, sg)
        # ③ 存量回补 LLM（movement 老熟人 / subgenre 特性前的老检出行）
        rest = [a for a in ids if a not in out]
        if rest:
            for aid, sg in (await db.execute(
                select(AppSubgenre.app_id, AppSubgenre.subgenre_cn)
                .where(AppSubgenre.app_id.in_(rest),
                       AppSubgenre.subgenre_cn.is_not(None))
            )).all():
                out.setdefault(aid, sg)
    return out
