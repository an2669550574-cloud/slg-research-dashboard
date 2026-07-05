"""竞品异动检测：每日同步后比对 game_rankings「今日 vs 上一可用日」。

零配额、零新基建——纯读已落库的 game_rankings；SLG 行里有显著变化就
**汇总成一条** logger.error，经现有 LoggingIntegration 推送 Sentry。

设计取舍：
- 一次 (country, platform) 只发一条事件（多条异动合并进同一条消息），
  沿用配额告警那套「合并单事件、不刷屏」的纪律。
- 无可比对的历史日（冷库 / 首次同步）→ 静默跳过，绝不发空告警。
- 只看 TopN 内：榜尾对竞品监控无意义，且收入仅 Top20 有值。
- 只比对 is_slg 行：用户要的是 SLG 竞品动向，不是策略榜全量噪声。
- 仅定时任务路径调用（见 scheduler._scheduled_sync）；手动 refresh /
  trigger 走裸 sync，不触发，避免每次刷新都告警。
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import distinct, select
from app.config import settings
from app.database import AsyncSessionLocal
from app.models.game import GameRanking, CHART_GROSSING
from app.services.slg_publishers import is_slg

logger = logging.getLogger(__name__)


async def detect_movement(country: str, platform: str, today: str) -> dict:
    """**纯检测**——比对今日与上一可用日，返回结构化异动摘要。无任何副作用，
    可被 API endpoint 任意频次调用（不会刷 Sentry）。
    定时任务路径需要告警的话，调用 `detect_and_alert_movement` 走带 emit 的包装。
    """
    summary = {
        "country": country, "platform": platform, "today": today,
        "prev_date": None,
        # ST 配额耗尽 / 同步失败导致今日 game_rankings 为空或严重不完整时,
        # 必须跳过对比——否则前一日 TopN SLG 全员会被错报为"跌出 TOP"。
        # 这是真实事故现场观察到的(2026-05-22 配额耗尽,UI 一次涌出 20+ 假跌出)。
        "today_missing": False,
        "new_entrants": [], "surges": [],
        "drops": [], "revenue_spikes": [],
        # 连涨趋势（sustained climb）：多日稳步累计爬升，补 surge 单日阈值盲区（见 _sustained_climbs）。
        "climbs": [],
    }
    if not settings.COMPETITOR_ALERT_ENABLED:
        return summary

    topn = settings.COMPETITOR_ALERT_TOPN
    jump = settings.COMPETITOR_RANK_JUMP
    rev_pct = settings.COMPETITOR_REVENUE_PCT
    rev_topn = settings.COMPETITOR_REVENUE_TOPN

    async with AsyncSessionLocal() as db:
        # 上一可用日：不一定是昨天（scheduler 可能漏过几天），取 < today 的最近一天。
        prev_date = (await db.execute(
            select(GameRanking.date).where(
                GameRanking.country == country,
                GameRanking.platform == platform,
                GameRanking.chart_type == CHART_GROSSING,
                GameRanking.date < today,
            ).order_by(GameRanking.date.desc()).limit(1)
        )).scalar_one_or_none()
        if not prev_date:
            return summary
        summary["prev_date"] = prev_date

        async def _rows(date):
            res = await db.execute(select(GameRanking).where(
                GameRanking.country == country,
                GameRanking.platform == platform,
                GameRanking.chart_type == CHART_GROSSING,
                GameRanking.date == date,
            ))
            return res.scalars().all()

        today_rows = await _rows(today)
        prev_rows = await _rows(prev_date)

        # 「回归」判定（is_reentry）：上一可用日**之前** window 天内曾在 TopN 的 app_id 集合。
        # new_entrant 默认只比 today vs 上一可用日两快照，老 SLG 短暂跌出 TopN 又回来会被错标
        # 「🆕 空降」（prod 实测 US/iOS top 榜 ~32% app 有出榜又回缺口）。命中本集合 → is_reentry，
        # 渲染层改「🔄 重回」+ 重要度降权、不污染今日要闻。窗口取 [cutoff, prev_date) 避开当期对比日
        # （在 prev_date 仍在榜就不会是 new_entrant）。COMPETITOR_REENTRY_WINDOW_DAYS=0 关此判定。
        reentry_ids: set[str] = set()
        win = settings.COMPETITOR_REENTRY_WINDOW_DAYS
        if win > 0:
            cutoff = (datetime.strptime(prev_date, "%Y-%m-%d")
                      - timedelta(days=win)).strftime("%Y-%m-%d")
            reentry_ids = set((await db.execute(
                select(distinct(GameRanking.app_id)).where(
                    GameRanking.country == country,
                    GameRanking.platform == platform,
                    GameRanking.chart_type == CHART_GROSSING,
                    GameRanking.rank.is_not(None),
                    GameRanking.rank <= topn,
                    GameRanking.date >= cutoff,
                    GameRanking.date < prev_date,
                )
            )).scalars().all())

    # 缺数据闸门:今日为空 / 严重少于昨天 → 标记不参与对比,router 把这些 combo
    # 单独放到 stale 列表里给前端展示"今日未同步",而不是错报满屏跌出。
    # 纯相对阈值(不少于昨天的 30%):同步出问题时今日行数一般断崖式归零,30%
    # 既能抓到这类典型场景,又不会误伤小榜或合成测试夹具。
    if not today_rows or len(today_rows) < 0.3 * len(prev_rows):
        summary["today_missing"] = True
        return summary

    prev = {r.app_id: r for r in prev_rows}
    cur = {r.app_id: r for r in today_rows}

    def _label(r):
        return r.name or r.app_id

    # 今日名次 TopN(topn) 内的 SLG：新进 / 窜升。app_id/icon 一并带出供前端跳转和展示。
    # 收入异动**不在此循环**——它另走更宽的 rev_topn（见下），与名次收窄解耦。
    for r in today_rows:
        if r.rank is None or r.rank > topn or not is_slg(r.app_id, r.publisher):
            continue
        p = prev.get(r.app_id)
        if p is None or p.rank is None or p.rank > topn:
            summary["new_entrants"].append({
                "app_id": r.app_id, "name": _label(r), "icon_url": r.icon_url,
                "prev_rank": p.rank if p else None, "cur_rank": r.rank,
                "publisher": r.publisher, "revenue": r.revenue, "downloads": r.downloads,
                # 回归判定：window 内曾在 TopN → 老游戏「重回」而非真「空降」（见上 reentry_ids）。
                "is_reentry": r.app_id in reentry_ids,
            })
            continue
        if p.rank - r.rank >= jump:
            summary["surges"].append({
                "app_id": r.app_id, "name": _label(r), "icon_url": r.icon_url,
                "prev_rank": p.rank, "cur_rank": r.rank,
                "publisher": r.publisher, "revenue": r.revenue, "downloads": r.downloads,
            })

    # 收入异动：**独立、更宽的 TopN（rev_topn，默认 20 = ST 供 revenue 的上限）**。收入大幅变动是
    # 高信号竞品动态，即便名次落在收窄后的 rank TopN(topn) 之外也值得报（2026-07-05 用户裁定，起因
    # #200 把名次 TopN 收到 15 曾误伤 #16–20 的收入异动）。与名次异动解耦、各自视角：同一 app 可
    # 既空降/窜升又收入异动，渲染层不跨类去重（本就允许 surge+revenue 双行）。prev 名次 >rev_topn 者
    # revenue 为 None（ST 只供 TopN 内）→ 被下方 revenue 空值守卫天然过滤。
    for r in today_rows:
        if r.rank is None or r.rank > rev_topn or not is_slg(r.app_id, r.publisher):
            continue
        p = prev.get(r.app_id)
        if p is None or not p.revenue or p.revenue <= 0 or r.revenue is None:
            continue
        pct = (r.revenue - p.revenue) / p.revenue * 100
        if abs(pct) >= rev_pct:
            summary["revenue_spikes"].append({
                "app_id": r.app_id, "name": _label(r), "icon_url": r.icon_url,
                "cur_rank": r.rank,  # 收入异动行带上当前名次，给收入涨跌一个排名参照系
                "prev_revenue": p.revenue, "cur_revenue": r.revenue, "pct": pct,
            })

    # 上一日在 TopN 的 SLG：今日跌出 TopN / 彻底掉榜
    for p in prev_rows:
        if p.rank is None or p.rank > topn or not is_slg(p.app_id, p.publisher):
            continue
        c = cur.get(p.app_id)
        if c is None or c.rank is None or c.rank > topn:
            src = c if c is not None else p  # 现状指标优先取今日行；彻底掉榜则退回昨日行
            summary["drops"].append({
                "app_id": p.app_id, "name": _label(p), "icon_url": p.icon_url,
                "prev_rank": p.rank, "cur_rank": c.rank if c else None,
                "publisher": p.publisher, "revenue": src.revenue, "downloads": src.downloads,
            })

    # 连涨趋势：另开一轮窗口历史查询（今日快照已确认非缺失，可安全对比）。已被今日「空降/
    # 窜升」占用的 app 排除——同一竞品不在两段各报一遍。
    exclude = ({e["app_id"] for e in summary["new_entrants"]}
               | {e["app_id"] for e in summary["surges"]})
    summary["climbs"] = await _sustained_climbs(
        country, platform, today, exclude=exclude, cur=cur)

    return summary


async def _sustained_climbs(country: str, platform: str, today: str, *,
                            exclude: set[str], cur: dict) -> list[dict]:
    """连涨趋势检测：窗口内**稳步累计爬升**的 SLG 竞品——补 surge「今日 vs 上一可用日」单日
    阈值的盲区（#40→#38→#35→#28，单日最多升 7、够不到 RANK_JUMP=10，被日间 diff 漏掉，
    但 5 天累计升 12 有意义）。零 ST，纯读 game_rankings 窗口历史。

    判定（全部满足）：
    - 今日名次 ≤ CLIMB_TOPN（比 surge 的 TopN 宽：中段向上爬才是连涨主场）；
    - is_slg；未被今日空降/窜升占用（exclude，去重）；
    - 窗口 [today-WINDOW, today] 内 ≥ MIN_SNAPSHOTS 个快照（数据充分性：稀疏市场自动跳过）；
    - 累计升幅 start_rank - cur_rank ≥ MIN_DROP；
    - 今日是窗口新高（cur_rank ≤ 窗口内最好名次）——排除「曾爬到高位又回落」的伪连涨；
    - **起点即窗口最差**（start_rank == 窗口内最差名次）——排除「先跌破起点再净回升」的 V 形/
      震荡（净上行但非稳步爬升，「连涨」文案会误导）；真实样本 Z Route 的内部小抖动仍 ≤ 起点、放行；
    - **无任何单日 surge**（相邻快照升幅全 < RANK_JUMP）——保证与 surge 段零重叠、不重报
      已被当日窜升报过的大跳。
    """
    if not settings.COMPETITOR_CLIMB_ENABLED:
        return []
    win_days = settings.COMPETITOR_CLIMB_WINDOW_DAYS
    min_drop = settings.COMPETITOR_CLIMB_MIN_DROP
    if win_days <= 0 or min_drop <= 0:
        return []
    climb_topn = settings.COMPETITOR_CLIMB_TOPN
    min_snaps = settings.COMPETITOR_CLIMB_MIN_SNAPSHOTS
    jump = settings.COMPETITOR_RANK_JUMP

    # 今日 TopN_climb 内、SLG、未被空降/窜升占用的候选（cur 已是今日行，含 is_slg 所需 publisher）。
    cands = {aid: r for aid, r in cur.items()
             if r.rank is not None and r.rank <= climb_topn
             and aid not in exclude and is_slg(r.app_id, r.publisher)}
    if not cands:
        return []

    cutoff = (datetime.strptime(today, "%Y-%m-%d")
              - timedelta(days=win_days)).strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(GameRanking.app_id, GameRanking.date, GameRanking.rank).where(
                GameRanking.country == country,
                GameRanking.platform == platform,
                GameRanking.chart_type == CHART_GROSSING,
                GameRanking.app_id.in_(list(cands)),
                GameRanking.rank.is_not(None),
                GameRanking.date >= cutoff,
                GameRanking.date <= today,
            )
        )).all()

    hist: dict[str, list[tuple[str, int]]] = {}
    for aid, d, rk in rows:
        hist.setdefault(aid, []).append((d, rk))

    climbs: list[dict] = []
    for aid, r in cands.items():
        seq = sorted(hist.get(aid, []))           # 按日期升序（含今日）
        if len(seq) < min_snaps:
            continue
        ranks = [rk for _, rk in seq]
        start_rank, cur_rank = ranks[0], r.rank
        if start_rank - cur_rank < min_drop:      # 累计升幅不足
            continue
        if cur_rank > min(ranks):                 # 今日非窗口新高 → 已回落，不算「正在连涨」
            continue
        if start_rank != max(ranks):              # 起点非窗口最差 → 中途曾跌破起点（V 形/震荡），非稳步
            continue
        if any((ranks[i] - ranks[i + 1]) >= jump  # 含单日 surge → 已被 surge 段报过，不重复
               for i in range(len(ranks) - 1)):
            continue
        # span_days 用**日历跨度**（start_date→today）而非 len(seq)：渲染文案是「N天累计」，
        # 若某几天漏同步（scheduler 可能漏过几天），快照数会少于真实天数，「N天」就失真。
        # 日历跨度对漏同步天鲁棒；无漏同步时与快照数一致（既有测试值不变）。min_snaps 仍用快照数。
        span_days = ((datetime.strptime(today, "%Y-%m-%d")
                      - datetime.strptime(seq[0][0], "%Y-%m-%d")).days + 1)
        climbs.append({
            "app_id": aid, "name": r.name or aid, "icon_url": r.icon_url,
            "start_rank": start_rank, "cur_rank": cur_rank,
            "start_date": seq[0][0], "span_days": span_days,
            "publisher": r.publisher, "revenue": r.revenue, "downloads": r.downloads,
        })
    return climbs


async def detect_and_alert_movement(country: str, platform: str, today: str) -> dict:
    """定时任务路径专用：检测 + 发 Sentry 告警。手动 refresh / API 拉取不应走这里。
    钉钉推送不在此（2026-06-12 起异动并入日级汇总，见 release_alerts.send_daily_digest）。"""
    summary = await detect_movement(country, platform, today)
    _emit(summary)
    return summary


def _format_parts(s: dict) -> list[str]:
    """异动摘要行（机器码口径，仅 Sentry 日志用；群消息人话版在 release_alerts）。"""
    parts = []
    for e in s["new_entrants"]:
        frm = "榜外" if e["prev_rank"] is None else f"#{e['prev_rank']}"
        parts.append(f"[NEW] {e['name']} 新进Top榜 ({frm}->#{e['cur_rank']})")
    for e in s["surges"]:
        parts.append(f"[UP] {e['name']} #{e['prev_rank']}->#{e['cur_rank']} (升{e['prev_rank'] - e['cur_rank']})")
    for e in s.get("climbs", []):
        parts.append(f"[CLIMB] {e['name']} 连涨 #{e['start_rank']}->#{e['cur_rank']} ({e['span_days']}天累计升{e['start_rank'] - e['cur_rank']})")
    for e in s["drops"]:
        to = "榜外" if e["cur_rank"] is None else f"#{e['cur_rank']}"
        parts.append(f"[DOWN] {e['name']} 跌出Top榜 (#{e['prev_rank']}->{to})")
    for e in s["revenue_spikes"]:
        parts.append(f"[REV] {e['name']} 收入{e['pct']:+.0f}% (${e['prev_revenue']:,.0f}->${e['cur_revenue']:,.0f})")
    return parts


def _emit(s: dict) -> None:
    parts = _format_parts(s)
    if not parts:
        logger.info(
            "Competitor movement %s/%s %s vs %s: no significant SLG movement",
            s["country"], s["platform"], s["today"], s["prev_date"],
        )
        return
    logger.error(
        "[COMPETITOR-MOVEMENT] %s/%s %s vs %s — %d 项SLG异动:\n%s",
        s["country"], s["platform"], s["today"], s["prev_date"], len(parts),
        "\n".join("  - " + p for p in parts),
    )

