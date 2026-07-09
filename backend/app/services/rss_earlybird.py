"""RSS 早鸟信号层（ADR 0005）：次市场 iOS 策略畅销榜的零 ST 日级新品信号。

ST 快照对次市场（JP/KR）双周一拍，新品上榜到被检出平均滞后 ~7 天、最坏 14 天——
恰好错过软启动/首发窗（买量调研最值钱的窗口）。加密 ST 同步被配额宪法禁止；
Apple **旧版分类维度 RSS**（`itunes.apple.com/{cc}/rss/topgrossingapplications/
limit=N/genre=7017/json`）经 2026-07-09 探针验证仍在服务、genre 参数真实生效、
日更、免费无鉴权——用它做每日早鸟补偿。

设计要点：
- **绝不写 game_rankings**：RSS 榜与 ST 榜口径不同源（RSS 无收入/下载估算、
  封顶 100），混写会污染 baseline/movement/走势的快照语义。独立台账
  `rss_chart_seen` 记「已见」。
- 首轮基线不报（is_baseline，与 itunes_releases 同哲学）；之后每日 diff，
  新面孔还要过三道闸：① ST 已见（该国 iOS game_rankings 任意时期出现过 =
  老面孔）② 检出已见（market_newcomer_log 该国 iOS 已有行）③ 忽略名单。
- 真早鸟写 market_newcomer_log **影子行**（chart_type='rss'，radar 同款范式）：
  riding 免费富化 + 中文化 + 子品类 + 视频管道；/history 排除不进市场卡片网格，
  分发走维护者卡「⚡ RSS 早鸟」段（领导卡不加，减量宪法）。
- 旧版 RSS 是弃用状态的遗留服务，随时可能消失——单国失败只降级该国，
  异常不拖垮 digest；全 404 时功能自然静默（台账停更、段不渲染）。

零 ST；每日 len(RSS_EARLYBIRD_COUNTRIES) 次免费 Apple 请求。
"""
import asyncio
import logging
from typing import Optional

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.game import GameRanking
from app.models.newcomer import MarketNewcomerLog, RssChartSeen

logger = logging.getLogger(__name__)

# 影子行检出通道标记（chart_type）。与 radar 同款：不是 ST 真榜，/history 显式排除。
CHART_RSS = "rss"

RSS_URL = ("https://itunes.apple.com/{cc}/rss/topgrossingapplications/"
           "limit={limit}/genre=7017/json")

# 每轮最多富化多少个真早鸟（免费 iTunes lookup，礼貌限速）。真早鸟日常量级是
# 个位数；防御异常日（如 Apple 改版返回全新 id 体系）一次喷太多请求。
_ENRICH_CAP_PER_RUN = 10
_POLITE_DELAY_S = 2.0


def _parse_entries(payload) -> list[dict]:
    """旧版 RSS JSON → [{app_id, name, publisher, rank}]。

    单条 entry 时 Apple 返回 dict 而非 list（旧版 RSS 的经典坑），统一包成 list。
    缺 id 的条目跳过（诚实丢弃，不编造）。rank = 榜序（1 起）。
    """
    feed = (payload or {}).get("feed") or {}
    entries = feed.get("entry") or []
    if isinstance(entries, dict):
        entries = [entries]
    out = []
    for i, e in enumerate(entries):
        try:
            aid = e["id"]["attributes"]["im:id"]
        except (KeyError, TypeError):
            continue
        name = ((e.get("im:name") or {}).get("label") or "").strip() or str(aid)
        artist = ((e.get("im:artist") or {}).get("label") or "").strip() or None
        out.append({"app_id": str(aid), "name": name, "publisher": artist, "rank": i + 1})
    return out


async def _fetch_chart(cc: str, limit: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(RSS_URL.format(cc=cc.lower(), limit=limit))
        r.raise_for_status()
        return _parse_entries(r.json())


async def sync_rss_earlybird() -> dict:
    """拉一轮各国 RSS 榜 → diff 台账 → 真早鸟落影子行。返回
    {fetched, new, baseline, items}；items = 本轮真早鸟（供 digest 段直接渲染，
    misfire 补跑时台账已见 → items 空 → 段不重复推）。"""
    out: dict = {"fetched": 0, "new": 0, "baseline": 0, "items": []}
    countries = [c.strip().upper() for c in settings.RSS_EARLYBIRD_COUNTRIES.split(",")
                 if c.strip()]
    if settings.USE_MOCK_DATA or not countries:
        return out

    from app.services.newcomer_log import enrich_fields, slg_app_ids_known
    from app.services.newcomers import _is_ignored, _load_ignore_keys
    from app.services.slg_publishers import is_slg

    today = utcnow_naive().strftime("%Y-%m-%d")
    now = utcnow_naive()
    ignore_pub_keys, ignore_app_ids = await _load_ignore_keys()
    enriched_this_run = 0

    for country in countries:
        try:
            entries = await _fetch_chart(country, settings.RSS_EARLYBIRD_LIMIT)
        except Exception:
            # 旧版 RSS 随时可能退役/抖动：单国失败只降级该国，不拖垮 digest。
            logger.warning("rss earlybird fetch failed for %s", country, exc_info=True)
            continue
        if not entries:
            continue
        out["fetched"] += len(entries)

        async with AsyncSessionLocal() as db:
            seen: dict[str, RssChartSeen] = {
                r.app_id: r for r in (await db.execute(
                    select(RssChartSeen).where(RssChartSeen.country == country)
                )).scalars().all()}
            first_run = not seen
            # 闸① ST 已见：该国 iOS 任意榜任意时期出现过 = ST 视角的老面孔，
            # RSS 只补 ST 没见过的（这也让首轮之后的日常 diff 极少误报）。
            st_known = set((await db.execute(
                select(GameRanking.app_id).where(
                    GameRanking.country == country,
                    GameRanking.platform == "ios").distinct()
            )).scalars().all())
            # 闸② 检出已见：market_newcomer_log 该国 iOS 已有行（含 radar/rss 影子）。
            log_known = set((await db.execute(
                select(MarketNewcomerLog.app_id).where(
                    MarketNewcomerLog.country == country,
                    MarketNewcomerLog.platform == "ios").distinct()
            )).scalars().all())
            known_slg = await slg_app_ids_known({e["app_id"] for e in entries})

            for e in entries:
                aid = e["app_id"]
                row = seen.get(aid)
                if row is not None:
                    row.last_seen_date = today
                    row.last_rank = e["rank"]
                    continue
                # 闸③ 忽略名单（人工确认非 SLG）。首轮全员按基线收编不报。
                is_new_signal = (not first_run
                                 and aid not in st_known
                                 and aid not in log_known
                                 and not _is_ignored(aid, e["publisher"],
                                                     ignore_pub_keys, ignore_app_ids))
                db.add(RssChartSeen(
                    country=country, app_id=aid, name=e["name"],
                    publisher=e["publisher"], first_seen_date=today,
                    first_rank=e["rank"], last_seen_date=today, last_rank=e["rank"],
                    is_baseline=not is_new_signal))
                if not is_new_signal:
                    out["baseline"] += 1
                    continue
                # 真早鸟 → market_newcomer_log 影子行：riding 富化/翻译/子品类/视频管道。
                enriched: Optional[dict] = None
                if enriched_this_run < _ENRICH_CAP_PER_RUN:
                    if enriched_this_run > 0:
                        await asyncio.sleep(_POLITE_DELAY_S)
                    enriched = await enrich_fields(aid, country.lower(), "ios")
                    enriched_this_run += 1
                row_slg = is_slg(aid, e["publisher"]) or aid in known_slg
                log_row = MarketNewcomerLog(
                    country=country, platform="ios", app_id=aid, chart_type=CHART_RSS,
                    as_of=today, name=e["name"], publisher=e["publisher"],
                    rank=e["rank"], revenue=None, is_slg=row_slg,
                    is_reentry=False, first_detected_at=now,
                    **(enriched or {}))
                if enriched:
                    log_row.enriched_at = now
                db.add(log_row)
                out["new"] += 1
                out["items"].append({
                    "country": country, "app_id": aid, "name": e["name"],
                    "publisher": e["publisher"], "rank": e["rank"], "is_slg": row_slg,
                })
            await db.commit()

    if out["new"] or out["baseline"]:
        logger.info("rss earlybird: fetched=%s new=%s baseline=%s",
                    out["fetched"], out["new"], out["baseline"])
    return out
