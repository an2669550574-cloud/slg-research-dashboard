"""AI 标签分析（P6）：对当前筛选范围的素材「结构化标签 + 已有 AI 分析内容」做
对话式分析。两用——一键报告（mode=report）+ 自由追问（mode=chat，多轮）。

设计要点：
- scope 与素材列表同口径（app_id + material_type + 分面筛选 tag_options），复用
  tagging.apply_facet_filter，零 Sensor Tower 配额。
- 额度护栏：命中素材数 > MATERIAL_LIMIT 直接拒绝（带素材 AI 分析内容很费 token，
  超限要求先缩小筛选）；命中 0 条也拒。
- 喂给 LLM 的数据层：① 标签分布聚合 ② 每条素材标签明细 ③ 每条素材已有 AI 分析
  （brief/tags/scenes/hooks）。走公司统一 LLM 网关（relay.tuyoo.com，OpenAI 兼容）。
- 回答是 markdown 自由文本（非 JSON）。会话 + 消息落库可回查；导出 md/csv。
- 分析建议遵循买量素材方法论硬约束（借结构不抄壳、反宏大叙事/CG 片等），写进
  system prompt，避免泛泛而谈。
"""
from __future__ import annotations

import csv
import io
import logging
from collections import Counter
from datetime import date, datetime
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.material import Material
from app.models.tag_analysis import TagAnalysisSession, TagAnalysisMessage
from app.database import utcnow_naive
from app.services import llm_gateway, tagging
from app.services.creative_adapt import HARD_CONSTRAINTS

logger = logging.getLogger(__name__)

# 一次分析最多覆盖多少条素材。带「素材 AI 分析内容」每条可能上千 token，50 条
# 已是 sonnet 上下文与单次成本的合理上限；超过要求用户先缩小筛选范围（省额度的硬闸）。
MATERIAL_LIMIT = 50

# 允许的网关模型白名单（与创意迁移一致：sonnet 省 / opus 强）。传白名单外一律拒。
ALLOWED_MODELS = ("claude-sonnet-4.5", "claude-opus-4.7")

# 成本干跑预估时的输出 token 估值：一份结构化报告的典型规模（宁高勿低）。
# 真实 max_tokens=4000，报告通常用不满；取 2200 作为「约 $X」的展示口径。
ESTIMATE_OUTPUT_TOKENS = 2200

# 报告模式的内置指令（用户不打字，点按钮即用此指令）。
REPORT_INSTRUCTION = (
    "请基于以下「当前筛选范围的标签数据 + 素材 AI 分析内容」，生成一份结构化的买量"
    "素材标签分析报告。要求：\n"
    "1. 先给整体概览（覆盖素材数、标签覆盖度、最突出的标签组合）。\n"
    "2. 按一级标签维度逐个分析分布特征，指出集中/缺口/异常。\n"
    "3. 结合素材的 AI 分析内容（脚本/钩子/场景），归纳「什么样的标签组合对应什么"
    "创意套路」，给出可复用的洞察。\n"
    "4. 最后给 2-4 条可执行建议（下一步该补哪类素材 / 测哪个方向）。\n"
    "用 markdown 输出，结论要落在给定数据上，禁止编造数据里没有的素材或数字。"
)

# system prompt：分析师角色 + 方法论硬约束 + 接地气约束（不许编数据）。
_ANALYSIS_SYSTEM = """你是资深 SLG 买量素材分析师，擅长从一批竞品素材的「结构化标签 + 创意分析」里读出可复用的买量规律。

我会给你一段【当前分析范围的标签数据】，含：
- 标签分布聚合（每个一级标签下各二级标签命中多少条素材）
- 每条素材的标签明细 + 它已有的 AI 创意分析（brief / 题材标签 / 分镜场景 / 钩子）

你的任务：基于这批真实数据做分析、回答用户问题或出报告。铁律：
- **只依据给定数据**：所有结论必须能在数据里找到支撑，禁止编造不存在的素材、数字或标签。数据没覆盖的就如实说「数据不足」。
- **结合创意内容，不只数标签**：把标签分布和素材的脚本/钩子/场景联系起来看，给出「这类标签组合通常对应哪种创意套路」这种有信息量的洞察。
- 涉及创意方向建议时，遵循买量素材方法论：借结构不抄壳、前 3 秒小场景切入、承接真实玩法；并规避以下常见毛病：
""" + "\n".join(f"  {i+1}. {c}" for i, c in enumerate(HARD_CONSTRAINTS)) + """
- 输出用中文 markdown，结构清晰（标题/列表/必要时表格）。语气专业、直给，不要寒暄。"""


# ─────────────────────────────────────────────────────────────────
# scope 与上下文构建
# ─────────────────────────────────────────────────────────────────

def _scope_select(app_id: Optional[str], material_type: Optional[str]):
    scope = select(Material.id)
    if app_id:
        scope = scope.where(Material.app_id == app_id)
    if material_type:
        scope = scope.where(Material.material_type == material_type)
    return scope


async def gather_scope_materials(
    db: AsyncSession, app_id: Optional[str], material_type: Optional[str], tag_options: Optional[str]
) -> list[Material]:
    """取范围内素材（含护栏）。raises ValueError：0 条 / 超 MATERIAL_LIMIT。"""
    scope = _scope_select(app_id, material_type)
    scope = await tagging.apply_facet_filter(db, scope, tag_options)
    count = (await db.execute(select(func.count()).select_from(scope.subquery()))).scalar_one()
    if count == 0:
        raise ValueError("当前筛选范围内没有素材，请调整筛选条件")
    if count > MATERIAL_LIMIT:
        raise ValueError(
            f"当前范围有 {count} 条素材，超过单次分析上限 {MATERIAL_LIMIT} 条。"
            f"带素材 AI 分析内容很费额度，请先用类型/标签筛选把范围缩到 {MATERIAL_LIMIT} 条以内再分析。"
        )
    rows = (await db.execute(
        select(Material).where(Material.id.in_(scope)).order_by(Material.created_at, Material.id)
    )).scalars().all()
    return list(rows)


def _fmt_date(d) -> str:
    if isinstance(d, (datetime, date)):
        return d.isoformat()[:10]
    return str(d) if d else ""


async def _tag_lines_by_material(
    db: AsyncSession, materials: list[Material]
) -> tuple[dict[int, list[str]], dict[int, list]]:
    """每条素材的标签明细文本行（维度名: 值）+ 原始标签项 map，供上下文块与分布统计。"""
    ids = [m.id for m in materials]
    tag_map = await tagging.load_tag_values_map(db, ids)
    out: dict[int, list[str]] = {}
    for mid, items in tag_map.items():
        # 同维度多个值合并成一行
        by_dim: dict[str, list[str]] = {}
        for it in items:
            v = it.value if it.value is not None else _fmt_date(it.value_date)
            if v:
                by_dim.setdefault(it.dimension_name, []).append(v)
        out[mid] = [f"{dim}: {'、'.join(vals)}" for dim, vals in by_dim.items()]
    return out, tag_map


def _distribution(tag_map: dict[int, list]) -> dict[str, Counter]:
    """从标签明细聚合「一级标签 → {二级值: 去重素材数}」。date 维度按具体日期计。"""
    dist: dict[str, Counter] = {}
    for items in tag_map.values():
        seen: set[tuple[str, str]] = set()
        for it in items:
            v = it.value if it.value is not None else _fmt_date(it.value_date)
            if not v:
                continue
            key = (it.dimension_name, v)
            if key in seen:
                continue
            seen.add(key)
            dist.setdefault(it.dimension_name, Counter())[v] += 1
    return dist


def _analysis_block(m: Material) -> str:
    """素材已有 AI 分析内容的紧凑文本（无分析则注明）。"""
    parts: list[str] = []
    if m.analysis_brief:
        parts.append(f"brief：{m.analysis_brief}")
    if m.analysis_tags:
        parts.append("题材标签：" + "、".join(str(x) for x in m.analysis_tags))
    if m.analysis_scenes:
        scenes = "；".join(
            f"[{s.get('ts', '?')}s]{s.get('description', '')}" for s in m.analysis_scenes[:8]
        )
        parts.append(f"分镜：{scenes}")
    if m.analysis_hooks:
        hooks = "；".join(
            f"[{h.get('ts', '?')}s]{h.get('kind', '?')}:{h.get('note', '')}" for h in m.analysis_hooks
        )
        parts.append(f"钩子：{hooks}")
    return "\n".join(f"  - {p}" for p in parts) if parts else "  - （该素材尚未做 AI 分析）"


async def build_context_block(db: AsyncSession, materials: list[Material]) -> tuple[str, dict[str, Counter]]:
    """拼「分布聚合 + 逐素材标签明细 + AI 分析」上下文块。返回 (文本, 分布字典)。"""
    tag_lines, tag_map = await _tag_lines_by_material(db, materials)
    dist = _distribution(tag_map)

    lines: list[str] = [f"## 一、标签分布聚合（共 {len(materials)} 条素材）"]
    if dist:
        for dim_name, counter in dist.items():
            ranked = "，".join(f"{v}×{n}" for v, n in counter.most_common())
            lines.append(f"- {dim_name}：{ranked}")
    else:
        lines.append("- （范围内素材尚未打任何结构化标签）")

    lines.append("\n## 二、逐素材明细（标签 + 已有 AI 分析）")
    for i, m in enumerate(materials, 1):
        title = m.title or f"素材{m.id}"
        tags = "；".join(tag_lines.get(m.id, [])) or "（未打标签）"
        lines.append(f"\n### [素材 {i}] {title}（{m.material_type}，上传 {_fmt_date(m.created_at)}）")
        lines.append(f"- 标签：{tags}")
        lines.append(_analysis_block(m))
    return "\n".join(lines), dist


# ─────────────────────────────────────────────────────────────────
# LLM 调用
# ─────────────────────────────────────────────────────────────────

def _validate_model(model: str) -> str:
    if model not in ALLOWED_MODELS:
        raise ValueError(f"不支持的模型：{model}；可选 {', '.join(ALLOWED_MODELS)}")
    return model


async def _call_llm(
    model: str, data_block: str, history: list[TagAnalysisMessage], user_text: str
) -> tuple[str, float, dict]:
    """发一轮对话。system=方法论+本轮范围数据；再接历史轮 + 新用户消息。
    返回 (assistant_markdown, cost_usd, usage_dict)。"""
    client = llm_gateway.get_client()
    system = (
        _ANALYSIS_SYSTEM
        + "\n\n---\n\n# 当前分析范围的标签数据\n"
        + data_block
    )
    messages = [{"role": "system", "content": system}]
    for h in history:
        if h.role in ("user", "assistant"):
            messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": user_text})

    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=4000,
        temperature=0.4,
    )
    answer = resp.choices[0].message.content or ""
    usage = llm_gateway.usage_to_dict(getattr(resp, "usage", None))
    cost = llm_gateway.estimate_cost(model, usage)
    return answer, cost.total_usd, {
        "input_tokens": cost.input_tokens, "output_tokens": cost.output_tokens,
    }


def _make_title(material_type: Optional[str], tag_options: Optional[str], n: int) -> str:
    type_label = {"video": "视频", "image": "图片", "playable": "可玩"}.get(material_type or "", "全部")
    suffix = "（已筛选）" if tag_options else ""
    return f"标签分析 · {type_label} · {n}条{suffix}"


# ─────────────────────────────────────────────────────────────────
# 公共 API
# ─────────────────────────────────────────────────────────────────

async def get_session(db: AsyncSession, session_id: int) -> TagAnalysisSession:
    s = (await db.execute(
        select(TagAnalysisSession).where(TagAnalysisSession.id == session_id)
    )).scalar_one_or_none()
    if not s:
        raise LookupError("分析会话不存在")
    return s


async def load_messages(db: AsyncSession, session_id: int) -> list[TagAnalysisMessage]:
    return list((await db.execute(
        select(TagAnalysisMessage).where(TagAnalysisMessage.session_id == session_id)
        .order_by(TagAnalysisMessage.id)
    )).scalars().all())


async def run_turn(
    db: AsyncSession,
    *,
    session_id: Optional[int],
    mode: str,
    message: Optional[str],
    model: str,
    app_id: Optional[str],
    material_type: Optional[str],
    tag_options: Optional[str],
) -> TagAnalysisSession:
    """跑一轮分析：新建会话（session_id 为空）或在既有会话追问。

    raises ValueError（模型非法/范围空或超限/追问空）、LookupError（会话不存在）。
    成功后持久化 user + assistant 两条消息并返回会话（调用方再带 messages 出参）。"""
    _validate_model(model)

    if session_id is not None:
        session = await get_session(db, session_id)
        app_id, material_type, tag_options = session.app_id, session.material_type, session.tag_options
        history = await load_messages(db, session_id)
    else:
        session = None
        history = []

    # scope 护栏（在写库前，避免建空会话）
    materials = await gather_scope_materials(db, app_id, material_type, tag_options)
    data_block, _ = await build_context_block(db, materials)

    if mode == "report":
        user_text = message.strip() if message and message.strip() else REPORT_INSTRUCTION
    else:
        if not message or not message.strip():
            raise ValueError("追问内容不能为空")
        user_text = message.strip()

    answer, cost, toks = await _call_llm(model, data_block, history, user_text)

    if session is None:
        session = TagAnalysisSession(
            title=_make_title(material_type, tag_options, len(materials)),
            app_id=app_id, material_type=material_type, tag_options=tag_options, model=model,
        )
        db.add(session)
        await db.flush()  # 拿到 session.id

    db.add(TagAnalysisMessage(session_id=session.id, role="user", content=user_text))
    db.add(TagAnalysisMessage(
        session_id=session.id, role="assistant", content=answer,
        model=model, cost_usd=cost,
        input_tokens=toks["input_tokens"], output_tokens=toks["output_tokens"],
        material_count=len(materials),
    ))
    session.updated_at = utcnow_naive()
    await db.commit()
    await db.refresh(session)
    return session


async def estimate_turn(
    db: AsyncSession,
    *,
    model: str,
    app_id: Optional[str],
    material_type: Optional[str],
    tag_options: Optional[str],
) -> dict:
    """干跑预估单次报告分析的成本（不打网关）。供模型下拉旁实时展示「约 $X」。

    与 run_turn 同口径数 scope，但**不抛**空/超限异常——返回 empty/over_limit 标志
    让前端转而提示护栏。命中合法时按 system+数据块+报告指令 估 input token、用
    ESTIMATE_OUTPUT_TOKENS 估 output，走 estimate_cost 折算美元（宁高勿低）。
    raises ValueError 仅在模型非法时。"""
    _validate_model(model)

    scope = _scope_select(app_id, material_type)
    scope = await tagging.apply_facet_filter(db, scope, tag_options)
    count = (await db.execute(select(func.count()).select_from(scope.subquery()))).scalar_one()

    base = {
        "material_count": int(count),
        "limit": MATERIAL_LIMIT,
        "empty": count == 0,
        "over_limit": count > MATERIAL_LIMIT,
        "model": model,
        "input_tokens_est": 0,
        "output_tokens_est": 0,
        "estimated_cost_usd": 0.0,
    }
    if count == 0 or count > MATERIAL_LIMIT:
        return base

    rows = (await db.execute(
        select(Material).where(Material.id.in_(scope)).order_by(Material.created_at, Material.id)
    )).scalars().all()
    data_block, _ = await build_context_block(db, list(rows))
    input_tokens = llm_gateway.rough_token_count(
        _ANALYSIS_SYSTEM + "\n\n" + data_block + "\n\n" + REPORT_INSTRUCTION
    )
    cost = llm_gateway.estimate_cost(model, {
        "prompt_tokens": input_tokens,
        "completion_tokens": ESTIMATE_OUTPUT_TOKENS,
    })
    base.update(
        input_tokens_est=input_tokens,
        output_tokens_est=ESTIMATE_OUTPUT_TOKENS,
        estimated_cost_usd=cost.total_usd,
    )
    return base


async def delete_session(db: AsyncSession, session_id: int) -> None:
    session = await get_session(db, session_id)
    # SQLite 不强制 FK 级联，应用层显式清子表（与项目其他删除套路一致）
    from sqlalchemy import delete as sa_delete
    await db.execute(sa_delete(TagAnalysisMessage).where(TagAnalysisMessage.session_id == session_id))
    await db.delete(session)
    await db.commit()


# ── 导出 ──────────────────────────────────────────────────────────────────

async def export_markdown(db: AsyncSession, session_id: int) -> str:
    """整段会话导出为 markdown（叙述结论）。"""
    session = await get_session(db, session_id)
    msgs = await load_messages(db, session_id)
    lines = [
        f"# {session.title}",
        "",
        f"- 模型：{session.model}",
        f"- 范围：{session.material_type or '全部类型'}"
        + (f"（含分面筛选 {session.tag_options}）" if session.tag_options else ""),
        f"- 生成时间：{_fmt_date(session.created_at)}",
        "",
    ]
    for m in msgs:
        if m.role == "user":
            lines.append(f"## 🧑 提问\n\n{m.content}\n")
        else:
            cost = f"（{m.model} · ${m.cost_usd:.4f} · {m.material_count} 条素材）" if m.cost_usd else ""
            lines.append(f"## 🤖 分析 {cost}\n\n{m.content}\n")
    return "\n".join(lines)


async def export_csv(db: AsyncSession, session_id: int) -> str:
    """标签分布数据导出为 CSV（按会话范围实时重算）。列：一级标签,二级标签,素材数。"""
    session = await get_session(db, session_id)
    materials = await gather_scope_materials(
        db, session.app_id, session.material_type, session.tag_options
    )
    _, tag_map = await _tag_lines_by_material(db, materials)
    dist = _distribution(tag_map)

    buf = io.StringIO()
    buf.write("﻿")  # BOM：Excel 正确识别 UTF-8 中文
    writer = csv.writer(buf)
    writer.writerow(["一级标签", "二级标签", "素材数"])
    for dim_name, counter in dist.items():
        for value, n in counter.most_common():
            writer.writerow([dim_name, value, n])
    return buf.getvalue()
