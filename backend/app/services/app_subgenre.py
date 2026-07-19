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
        sg, name_cn = await classify_subgenre(name, genre, desc)
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
