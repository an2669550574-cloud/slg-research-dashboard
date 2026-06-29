"""新品中文化（LLM 网关）。

商店描述是源区语言（日/韩/英/德/俄），团队读中文费劲。LLM 给新品按 app 翻一次：
- summary_cn：一句话「这是什么游戏」（题材+品类+卖点，≤约35字）→ digest 新品行 + 抽屉副标题 + 新品页卡片。
- description_cn：商店描述全文中译 → 抽屉展示、可切原文。

覆盖**所有待翻新品**（已识别 SLG + 待识别新厂），按 **app_id 去重**（同游戏跨 combo 多行
只翻一次、回写全部行）。**is_slg 优先排序**：已识别 SLG 先翻（digest/领导卡依赖），待识别新厂
用剩余每日 cap——这样把「待识别新厂」也中文化，新品页核查建档时看得懂（领导反馈非中文太多）。
**人工确认的忽略名单不翻**（省 LLM，这些本就不进待识别视图）。走太石网关（OpenAI 兼容）便宜
文本模型，cost 经 estimate_cost 记日志。USE_MOCK_DATA / 无 TAISHI_API_KEY → 整体 no-op。
每日封顶 NEWCOMER_TRANSLATE_DAILY_CAP 防烧成本。
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

# 玩法子品类受控词表（**按核心玩法机制，非题材**）。给「对标我方哪款」精确匹配用——题材关键词
# （末日/丧尸）横跨多品类分不出「数字门 SLG vs 基地建设 SLG」，靠这个机制维度区分。新增子品类
# 必须同步：本词表 + _PROMPT 定义 + 前端 ProductsManage 下拉 + own_products.match_subgenre 配置。
SUBGENRE_VOCAB = (
    "数字门SLG", "基地建设SLG", "国战SLG", "塔防", "三消合成",
    "城建模拟", "放置养成", "卡牌RPG", "休闲益智", "其他",
)

_PROMPT = """你是手游竞品调研助手。下面是一款游戏的应用商店信息。只输出 JSON（不要解释、不要代码围栏）：
{{"summary": "一句话简体中文，说清这是什么游戏（题材+品类+核心玩法卖点），不超过35字，不带书名号", "subgenre": "从下面固定列表选最贴切的一个核心玩法子品类(只填列表里的词)", "translation": "把下面的商店描述完整翻译成简体中文，保留分段，不增删内容"}}

子品类固定列表（**按核心玩法机制判定，不看题材**）：
- 数字门SLG：有「跑酷穿门、兵力数字增减(加减乘除)、滚雪球合成」前置小游戏，过关后回基地建设/PvP 的 SLG（Last War: Survival 类）
- 基地建设SLG：建避难所/城市、招英雄、出兵 PvP/联盟国战的传统 SLG，**无数字门跑酷前置**（State of Survival / Whiteout Survival / Last Shelter 类）
- 国战SLG：历史/文明大地图国战（Rise of Kingdoms / 三国类）
- 塔防：布阵/派兵守固定路线
- 三消合成：消除或合成为核心玩法
- 城建模拟：单机城市/家园建设经营、无 PvP 攻防（Frostpunk / 模拟经营类）
- 放置养成：挂机/放置为主
- 卡牌RPG：抽卡养成、回合/卡牌战斗
- 休闲益智：超休闲/益智小游戏
- 其他：都不贴切

游戏名：{name}
品类：{genre}
商店描述：
{description}"""


def _parse(content: str) -> dict | None:
    """容错解析 LLM 返回 JSON。两层兜底，关键是「截断也别让该行卡在永久重试」：

    1. 从第一个 `{` 起 `raw_decode`：只取首个合法 JSON 值、**忽略后面的脚注/散文**
       （便宜模型偶尔在 JSON 后加「注：…」，贪婪 `\\{.*\\}` 会把脚注的 `}` 也吞进来解析失败）。
    2. raw_decode 失败（多半是 max_tokens 截断了 translation）→ 至少**抢救 summary**
       （它在 JSON 最前、通常完整）。返回 {"summary": ...} 让调用方写回 summary_cn，
       该行从此退出「summary_cn IS NULL」重试集，不再每天空翻烧配额。
    """
    if not content:
        return None
    i = content.find("{")
    if i != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(content[i:])
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            pass
    m = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', content)
    if m:
        try:
            return {"summary": json.loads('"' + m.group(1) + '"')}   # 解转义
        except ValueError:
            return {"summary": m.group(1)}
    return None


async def translate_pending_newcomers(cap: int | None = None) -> int:
    """给未翻译的新品生成 summary_cn + description_cn，返回翻译的 app 数。

    取 description 非空、summary_cn 为空的行（**SLG + 待识别新厂都覆盖**），按 app_id 去重，
    每 app 一次 LLM 调用，回写该 app **全部** market_newcomer_log 行。**is_slg 优先排序**：
    已识别 SLG 先占 cap（digest/领导卡依赖），待识别新厂用剩余名额（多的下次接着翻）。
    **忽略名单内的行跳过**（人工确认非 SLG，不值当烧 LLM）。USE_MOCK_DATA / 无 key no-op。
    单 app 失败只跳过该 app（summary_cn 留 NULL，下次重试），不拖垮整轮。
    """
    if settings.USE_MOCK_DATA or not settings.TAISHI_API_KEY:
        return 0
    lim = cap if cap is not None else settings.NEWCOMER_TRANSLATE_DAILY_CAP
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(MarketNewcomerLog.app_id, MarketNewcomerLog.name,
                   MarketNewcomerLog.genre, MarketNewcomerLog.description,
                   MarketNewcomerLog.publisher, MarketNewcomerLog.is_slg)
            # 前进式：只翻 summary_cn 缺的（真·待翻）。subgenre_cn 与 summary 同一次调用产出，
            # 故新行天然带子品类；老行不回填——「对标我方哪款」已从题材关键词切到子品类相等匹配，
            # 老假阳行（subgenre_cn=NULL ≠「数字门SLG」）立即不再误标，无需回填（数据证近期 feed
            # 无数字门新品，回填也捞不到正例）。这样也避开「非词表 subgenre→NULL→每天重试烧配额」。
            .where(MarketNewcomerLog.description.is_not(None),
                   MarketNewcomerLog.summary_cn.is_(None))
            # is_slg 优先：已识别 SLG 先翻，待识别新厂用剩余 cap。
            .order_by(MarketNewcomerLog.is_slg.desc(), MarketNewcomerLog.id.desc())
        )).all()
    # 人工确认的非 SLG 噪声（忽略名单，与 /history、/gaps 同口径）不翻：省 LLM，
    # 且这些行本就被新品页过滤掉、不进待识别视图。
    from app.services.newcomers import _load_ignore_keys, _is_ignored
    ignore_pub_keys, ignore_app_ids = await _load_ignore_keys()
    # 按 app_id 去重（同游戏跨 combo 多行只翻一次），保序取最新（is_slg 优先）。
    seen: dict[str, tuple] = {}
    for app_id, name, genre, desc, publisher, _is_slg in rows:
        if _is_ignored(app_id, publisher, ignore_pub_keys, ignore_app_ids):
            continue
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
                # 描述截到 1500 字，全文中译 + 摘要的 CJK 输出可达 2K+ tokens；给足
                # 3500 防截断（截断会让 JSON 不完整、_parse 抢救只剩 summary、丢译文）。
                max_tokens=3500, temperature=0.2,
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
        # 玩法子品类：只收受控词表里的值（LLM 偶尔会编新词/带解释），非词表 → None（不脏库、
        # 精确匹配也不会误命中）。给「对标我方哪款」按机制精确匹配，治题材关键词太宽泛。
        sg_raw = str(parsed.get("subgenre") or "").strip()
        subgenre = sg_raw if sg_raw in SUBGENRE_VOCAB else None
        # 回写该 app 全部行（跨 combo/榜）。summary 本就 app 级、country 无关；
        # description_cn 用最新行的译文覆盖全部——同 app 跨国描述偶有差异，但这是竞品
        # 速览、headline 价值在 summary，按 app 翻一次省 LLM 是有意取舍（cost 硬上限
        # NEWCOMER_TRANSLATE_DAILY_CAP × flash 模型 < $0.15/天，不并入 LLM_DAILY_BUDGET）。
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(MarketNewcomerLog)
                .where(MarketNewcomerLog.app_id == app_id)
                .values(summary_cn=summary, description_cn=translation,
                        subgenre_cn=subgenre))
            await db.commit()
        done += 1
    if done:
        logger.info("newcomer translate: %d app(s), est $%.4f", done, total_usd)
    return done
