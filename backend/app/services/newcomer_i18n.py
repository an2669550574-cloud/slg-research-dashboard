"""新品中文化（LLM 网关）。

商店描述是源区语言（日/韩/英），团队读中文费劲。LLM 给 is_slg 新品按 app 翻一次：
- summary_cn：一句话「这是什么游戏」（题材+品类+卖点，≤约35字）→ digest 新品行 + 抽屉副标题。
- description_cn：商店描述全文中译 → 抽屉展示、可切原文。

只对 **is_slg** 新品、按 **app_id 去重**（同游戏跨 combo 多行只翻一次、回写全部行）。
走太石网关（OpenAI 兼容）便宜文本模型，cost 经 estimate_cost 记日志。USE_MOCK_DATA /
无 TAISHI_API_KEY → 整体 no-op。每日封顶 NEWCOMER_TRANSLATE_DAILY_CAP 防烧成本。
"""
import json
import logging
import re

from sqlalchemy import select, update

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.newcomer import MarketNewcomerLog
from app.services import llm_gateway

logger = logging.getLogger(__name__)

_PROMPT = """你是手游竞品调研助手。下面是一款游戏的应用商店信息。只输出 JSON（不要解释、不要代码围栏）：
{{"summary": "一句话简体中文，说清这是什么游戏（题材+品类+核心玩法卖点），不超过35字，不带书名号", "translation": "把下面的商店描述完整翻译成简体中文，保留分段，不增删内容"}}

游戏名：{name}
品类：{genre}
商店描述：
{description}"""


def _parse(content: str) -> dict | None:
    """容错解析 LLM 返回 JSON：抓第一个 {...} 块（去掉可能的 ```json 围栏/前后缀文字）。"""
    if not content:
        return None
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else None
    except (ValueError, TypeError):
        return None


async def translate_pending_newcomers(cap: int | None = None) -> int:
    """给未翻译的 is_slg 新品生成 summary_cn + description_cn，返回翻译的 app 数。

    取 description 非空、summary_cn 为空、is_slg 的行，按 app_id 去重，每 app 一次
    LLM 调用，回写该 app **全部** market_newcomer_log 行。USE_MOCK_DATA / 无 key no-op。
    单 app 失败只跳过该 app（summary_cn 留 NULL，下次重试），不拖垮整轮。
    """
    if settings.USE_MOCK_DATA or not settings.TAISHI_API_KEY:
        return 0
    lim = cap if cap is not None else settings.NEWCOMER_TRANSLATE_DAILY_CAP
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(MarketNewcomerLog.app_id, MarketNewcomerLog.name,
                   MarketNewcomerLog.genre, MarketNewcomerLog.description)
            .where(MarketNewcomerLog.is_slg.is_(True),
                   MarketNewcomerLog.description.is_not(None),
                   MarketNewcomerLog.summary_cn.is_(None))
            .order_by(MarketNewcomerLog.id.desc())
        )).all()
    # 按 app_id 去重（同游戏跨 combo 多行只翻一次），保序取最新。
    seen: dict[str, tuple] = {}
    for app_id, name, genre, desc in rows:
        seen.setdefault(app_id, (name, genre, desc))
    if not seen:
        return 0
    client = llm_gateway.get_client()
    model = settings.TAISHI_TEXT_MODEL
    done = 0
    total_usd = 0.0
    for app_id, (name, genre, desc) in list(seen.items())[:lim]:
        prompt = _PROMPT.format(name=name or "", genre=genre or "未知",
                                description=(desc or "")[:1500])
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1200, temperature=0.2,
            )
            content = (resp.choices[0].message.content or "") if resp.choices else ""
            total_usd += llm_gateway.estimate_cost(
                model, llm_gateway.usage_to_dict(getattr(resp, "usage", None))).total_usd
        except Exception:
            logger.warning("newcomer translate failed for %s", app_id, exc_info=True)
            continue
        parsed = _parse(content)
        if not parsed or not str(parsed.get("summary") or "").strip():
            continue
        summary = str(parsed["summary"]).strip()[:200]
        translation = str(parsed.get("translation") or "").strip() or None
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(MarketNewcomerLog)
                .where(MarketNewcomerLog.app_id == app_id)
                .values(summary_cn=summary, description_cn=translation))
            await db.commit()
        done += 1
    if done:
        logger.info("newcomer translate: %d app(s), est $%.4f", done, total_usd)
    return done
