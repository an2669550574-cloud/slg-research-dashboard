"""创意迁移：把已分析的参考素材 + 自家产品 brief → 可拍摄的创意方向 / 详细脚本。

两段式（人在中间）：
1. generate_directions(material, our_product) → 3-5 个方向卡片
2. 用户选定一个方向后：generate_script(material, direction, our_product) → 详细分镜

方法论参考公众号《开源买量素材skill》（作者：杰克 Ultra），核心要点：
- 不要"一键生成成片脚本"，3 个方向人挑 1 个再细化
- AI 写买量脚本的两大老毛病：① 喜欢宏大叙事 ② 一个镜头塞 5 件事
- 五条硬约束被写进 system prompt 并要求模型输出前自检

纯文本 LLM 调用，不带视觉，单次 ~$0.03（方向）/ ~$0.05（脚本）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.config import settings
from app.models.material import Material
from app.services import llm_gateway

logger = logging.getLogger(__name__)

# 创意迁移类调用允许的模型白名单（都是带视觉/强归纳的 Claude；纯文本任务用
# 不到视觉，但沿用 Claude 系保证创意质量）。跨素材统一方向默认 sonnet，
# 用户可在前端升到 opus。传白名单外的值一律拒绝（防止误调贵模型/不存在模型）。
ALLOWED_ADAPT_MODELS = ("claude-sonnet-4.5", "claude-opus-4.7")

# 跨素材归纳的输出体量基本固定（一份共性分析 + 3-5 方向），不随参考片数线性增长。
# 干跑成本预估按此估算 output tokens（宁高勿低）。
_UNIFIED_OUTPUT_TOKENS_EST = 3800


# 五条硬约束。作者原话基础上略整理；同时作为 prompt 的输出前自检 checklist。
HARD_CONSTRAINTS = [
    "禁止宏大叙事开场（远古战场/王国崩塌/多方势力对峙/命运之战 等都不行）",
    "禁止写成游戏 CG 宣传片（买量素材的本质是小场景切入 + 视觉爽点）",
    "每个镜头只展示一个核心情节（一镜一事）",
    "0-1.5 秒只表现一个动作（前 1.5s 内禁止两个以上独立动作叠加）",
    "若需要反馈/UI 提示/数值变化，必须**单独**给反馈镜头，不要塞在动作镜头里",
]

# 方向生成的 system prompt
_DIRECTIONS_SYSTEM = """你是资深 SLG 买量素材策划，专长把竞品爆款的"底层结构"迁移到不同产品上。
我会给你两段输入：

【参考素材分析】（已是结构化）
- brief: 一段总结
- tags: 题材/手法标签
- scenes: 时间轴上的场景描述
- hooks: 卸负/反转/CTA 等钩子

【自家产品 brief】
- 用户自由文本（题材、玩法、目标人群、卖点、差异化等）

任务：输出 3-5 个**可拍摄的创意方向**，核心原则：
- **借结构，不抄壳**：借用参考片的底层叙事结构（如：小场景采集 → 配方解锁 → 合成 → 落地），但题材/角色/视觉必须切换到自家产品语境
- **前 3 秒小场景切入**：每个方向必须给出明确的"前 3 秒画面"——禁止宏大叙事、禁止 CG 风
- **承接玩法**：结尾必须落到自家产品的真实玩法/系统/CTA，不要悬空

严格输出 JSON（直接输出对象本体，不要 markdown、不要解释）：

{
  "directions": [
    {
      "name": "5-10 字短标题",
      "concept": "一句话核心概念",
      "borrows_from_ref": "明确说从参考片哪段/哪个钩子借用什么底层结构（不抄具体题材）",
      "fit_to_self_product": "为什么这个方向适合自家产品（结合用户描述里的卖点/受众/差异化）",
      "opening_3sec": "前 3 秒的具体画面（必须遵守'小场景 + 一个动作'，禁宏大叙事）",
      "key_hooks": [
        {"ts_est": "5s", "kind": "卸负/反转/CTA/价值主张/情绪高潮/对比/痛点 之一", "note": "..."}
      ],
      "ending_cta": "结尾如何承接到自家产品的玩法/系统/CTA",
      "risk_notes": "本方向最容易跑偏的点（如：避免与参考片画面同质化、避免某个镜头落不了地）"
    }
  ],
  "constraints_check": {
    "no_grand_opening": "✓ 已遵守 / ✗ 违反原因：…",
    "no_cg_promo": "✓ / ✗ …",
    "one_event_per_shot": "✓ / ✗ …",
    "one_action_in_first_1_5s": "✓ / ✗ …",
    "feedback_separate_shot": "✓ / ✗ …"
  }
}

硬约束（输出前请逐条自检并写入 constraints_check）：
""" + "\n".join(f"{i+1}. {c}" for i, c in enumerate(HARD_CONSTRAINTS))


# 脚本生成的 system prompt
_SCRIPT_SYSTEM = """你是资深 SLG 买量素材分镜师。我会给你：
- 参考素材的结构化分析
- 自家产品 brief
- 用户选定的"创意方向"（含 opening_3sec / 钩子序列 / 结尾 CTA）

任务：把选定方向细化成**可制作的详细分镜脚本**。20-30 个镜头，总时长 25-35 秒。

严格输出 JSON：

{
  "direction_name": "选定方向的 name",
  "total_duration_sec": 30,
  "shots": [
    {
      "ts": "0-1.5s",
      "shot_type": "近景/中景/远景/特写/俯视 之一",
      "visual": "<具体画面描述：人物动作/环境/物体>",
      "audio_voiceover": "<口播文案 或 关键音效；没有就写「无」>",
      "production_notes": "<制作建议：转场/UI 叠加/动效。没有就写「无」>"
    }
  ],
  "constraints_check": {
    "no_grand_opening": "✓ / ✗ …",
    "no_cg_promo": "✓ / ✗ …",
    "one_event_per_shot": "✓ / ✗ …",
    "one_action_in_first_1_5s": "✓ / ✗ …",
    "feedback_separate_shot": "✓ / ✗ …"
  }
}

硬约束（输出前请逐镜头自检并写入 constraints_check）：
""" + "\n".join(f"{i+1}. {c}" for i, c in enumerate(HARD_CONSTRAINTS)) + """

补充：
- 时间码用闭区间「N-Ms」格式
- 0-1.5s 镜头必须只一个动作（自检"one_action_in_first_1_5s"严守）
- 反馈/数值/UI 必须单独镜头（自检"feedback_separate_shot"严守）
- visual 描述要可制作——避免"史诗般的"、"震撼的"这类抽象词
"""


# 跨素材统一方向的 system prompt。与单素材方向最大区别：先**归纳 N 片共性**，
# 再基于共性结构（而非某一片）产出迁移方向——目的是从一批爆款里提炼可复用的
# 底层套路，而不是把某一支照搬。
_UNIFIED_DIRECTIONS_SYSTEM = """你是资深 SLG 买量素材策划，专长从一**批**竞品爆款里提炼可复用的"底层结构"，再迁移到不同产品上。
我会给你两段输入：

【N 支参考素材分析】（每支已结构化：brief / tags / scenes / hooks，按 [素材 i] 编号）
【自家产品 brief】（用户自由文本：题材、玩法、目标人群、卖点、差异化等）

任务分两步：
1. **归纳共性**：找出这 N 支素材在底层叙事结构 / 钩子套路 / 节奏上的**共同点**（不是逐支复述，而是抽象出可复用的套路），并指出值得注意的差异。
2. **统一方向**：基于归纳出的共性结构（而非某一支），输出 3-5 个**可拍摄的创意方向**，核心原则：
   - **借结构，不抄壳**：借用这批片共有的底层结构，但题材/角色/视觉切换到自家产品语境
   - **前 3 秒小场景切入**：每个方向给明确的"前 3 秒画面"——禁宏大叙事、禁 CG 风
   - **承接玩法**：结尾落到自家产品的真实玩法/系统/CTA

严格输出 JSON（直接输出对象本体，不要 markdown、不要解释）：

{
  "common_patterns": {
    "shared_structure": "这批片共有的底层叙事结构（如：困境切入→资源/数值快速增长→繁荣对比→即点即玩 CTA）",
    "shared_hooks": ["跨片反复出现的钩子套路，逐条"],
    "shared_pacing": "共性节奏（如：30s 内困境到繁荣闭环、前 3s 强压迫）",
    "notable_variations": "各片值得注意的差异点（提示可差异化的空间）"
  },
  "directions": [
    {
      "name": "5-10 字短标题",
      "concept": "一句话核心概念",
      "borrows_from_refs": "明确说从这批片**共有**的哪段结构/钩子借用什么（不抄具体题材）",
      "fit_to_self_product": "为什么这个方向适合自家产品（结合用户描述里的卖点/受众/差异化）",
      "opening_3sec": "前 3 秒的具体画面（必须遵守'小场景 + 一个动作'，禁宏大叙事）",
      "key_hooks": [
        {"ts_est": "5s", "kind": "卸负/反转/CTA/价值主张/情绪高潮/对比/痛点 之一", "note": "..."}
      ],
      "ending_cta": "结尾如何承接到自家产品的玩法/系统/CTA",
      "risk_notes": "本方向最容易跑偏的点"
    }
  ],
  "constraints_check": {
    "no_grand_opening": "✓ 已遵守 / ✗ 违反原因：…",
    "no_cg_promo": "✓ / ✗ …",
    "one_event_per_shot": "✓ / ✗ …",
    "one_action_in_first_1_5s": "✓ / ✗ …",
    "feedback_separate_shot": "✓ / ✗ …"
  }
}

硬约束（输出前请逐条自检并写入 constraints_check）：
""" + "\n".join(f"{i+1}. {c}" for i, c in enumerate(HARD_CONSTRAINTS))


# ─────────────────────────────────────────────────────────────────
# 内部辅助
# ─────────────────────────────────────────────────────────────────

def _material_analysis_block(m: Material) -> str:
    """把 material 的 analysis_* 字段拼成给 LLM 的参考块。"""
    parts = []
    if m.analysis_brief:
        parts.append(f"## brief\n{m.analysis_brief}")
    if m.analysis_tags:
        parts.append("## tags\n" + ", ".join(m.analysis_tags))
    if m.analysis_scenes:
        scenes_txt = "\n".join(
            f"- [{s.get('ts', '?')}s] {s.get('description', '')}" for s in m.analysis_scenes
        )
        parts.append(f"## scenes\n{scenes_txt}")
    if m.analysis_hooks:
        hooks_txt = "\n".join(
            f"- [{h.get('ts', '?')}s] {h.get('kind', '?')}: {h.get('note', '')}"
            for h in m.analysis_hooks
        )
        parts.append(f"## hooks\n{hooks_txt}")
    return "\n\n".join(parts) if parts else "(参考素材尚未分析或分析为空)"


def _parse_json(text: str) -> dict:
    """与 video_analyze 同款三层兜底解析。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    if not text.startswith("{"):
        s = text.find("{")
        e = text.rfind("}")
        if s >= 0 and e > s:
            text = text[s : e + 1]
    return json.loads(text)


async def _call_text_llm(
    system: str, user: str, max_tokens: int = 6000, model: Optional[str] = None
) -> tuple[dict, float, str]:
    """纯文本 LLM 调用，返回 (parsed_json, cost, model)。

    max_tokens 默认 6000——方向生成 3-5 条 + checklist 一般 2-3K，
    脚本 20-30 镜头每镜头 ~100 字一般 4-5K；留余量避免截断导致 JSON 不闭合。
    model 为 None 时用 settings.TAISHI_VISION_MODEL（同款 sonnet）；跨素材方向
    可显式传 sonnet/opus（白名单由调用方校验）。
    """
    client = llm_gateway.get_client()
    model = model or settings.TAISHI_VISION_MODEL
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=0.6,  # 创意任务比分析略放开
    )
    text = resp.choices[0].message.content or ""
    finish = resp.choices[0].finish_reason
    usage = llm_gateway.usage_to_dict(getattr(resp, "usage", None))
    cost = llm_gateway.estimate_cost(model, usage).total_usd
    try:
        parsed = _parse_json(text)
    except json.JSONDecodeError as e:
        # 保留原文便于诊断（log 截断 + finish_reason 是关键线索）
        logger.error("LLM JSON parse failed (finish=%s, len=%d): %s\nFIRST 500: %s",
                     finish, len(text), e, text[:500])
        raise ValueError(
            f"模型返回非 JSON（finish_reason={finish}）；"
            f"通常是 max_tokens 太小被截断或被前后缀污染"
        ) from e
    return parsed, cost, model


# ─────────────────────────────────────────────────────────────────
# 公共 API
# ─────────────────────────────────────────────────────────────────

@dataclass
class AdaptResult:
    data: dict
    cost_usd: float
    model: str


async def generate_directions(material: Material, our_product: str) -> AdaptResult:
    """阶段 1：基于参考素材 + 自家产品 brief 生成 3-5 个创意方向。

    raises ValueError 当 material 尚未分析或我们产品为空。
    """
    if not material.analysis_brief and not material.analysis_scenes:
        raise ValueError("素材尚未完成分析，请先点击「开始分析」")
    if not our_product or not our_product.strip():
        raise ValueError("自家产品 brief 不能为空")
    user_msg = (
        "# 参考素材分析\n"
        f"{_material_analysis_block(material)}\n\n"
        "# 自家产品 brief（用户填写）\n"
        f"{our_product.strip()}\n\n"
        "请输出 3-5 个可拍摄的创意方向 + 五条硬约束的自检结果。"
    )
    parsed, cost, model = await _call_text_llm(_DIRECTIONS_SYSTEM, user_msg)
    return AdaptResult(data=parsed, cost_usd=cost, model=model)


async def generate_script(material: Material, our_product: str, direction: dict) -> AdaptResult:
    """阶段 2：基于选定方向写详细分镜脚本。"""
    if not direction or not isinstance(direction, dict) or not direction.get("name"):
        raise ValueError("direction 缺失或字段不全")
    if not our_product or not our_product.strip():
        raise ValueError("自家产品 brief 不能为空")
    user_msg = (
        "# 参考素材分析\n"
        f"{_material_analysis_block(material)}\n\n"
        "# 自家产品 brief（用户填写）\n"
        f"{our_product.strip()}\n\n"
        "# 选定方向\n"
        f"{json.dumps(direction, ensure_ascii=False, indent=2)}\n\n"
        "请把这个方向细化成 20-30 个镜头的可制作分镜脚本，"
        "每个镜头逐条自检五条硬约束。"
    )
    parsed, cost, model = await _call_text_llm(_SCRIPT_SYSTEM, user_msg)
    return AdaptResult(data=parsed, cost_usd=cost, model=model)


# ─────────────────────────────────────────────────────────────────
# 跨素材统一方向（选项 C）
# ─────────────────────────────────────────────────────────────────

# 一次最多归纳多少支素材：上限是为了 ① 防输入 token 膨胀烧钱 ② 避免上下文过长
# 摊薄归纳质量。≥2 才有"跨素材"意义。
MIN_UNIFIED_MATERIALS = 2
MAX_UNIFIED_MATERIALS = 15


def _build_unified_user_msg(materials: list[Material], our_product: str) -> str:
    """把 N 支素材的分析块拼成给 LLM 的 user message（带 [素材 i] 编号）。"""
    blocks = []
    for i, m in enumerate(materials, 1):
        title = m.title or f"素材{m.id}"
        blocks.append(f"## [素材 {i}] {title}\n{_material_analysis_block(m)}")
    refs = "\n\n".join(blocks)
    return (
        f"# {len(materials)} 支参考素材分析\n"
        f"{refs}\n\n"
        "# 自家产品 brief（用户填写）\n"
        f"{our_product.strip()}\n\n"
        "请先归纳这批素材的共性结构/钩子/节奏，再基于共性输出 3-5 个可拍摄的创意方向 + 五条硬约束自检。"
    )


def _validate_unified_inputs(
    materials: list[Material], our_product: str, model: str, require_product: bool = True
) -> None:
    """跨素材方向的入参校验（service 层兜底；router 层也会先校验给更友好的报错）。

    require_product=False 用于干跑成本预估——预估成本几乎不依赖产品 brief，
    放行空 brief 让前端一打开弹窗就能看到预估金额（无需先写产品描述）。
    """
    if model not in ALLOWED_ADAPT_MODELS:
        raise ValueError(f"不支持的模型：{model}；可选 {', '.join(ALLOWED_ADAPT_MODELS)}")
    if require_product and (not our_product or not our_product.strip()):
        raise ValueError("自家产品 brief 不能为空")
    n = len(materials)
    if n < MIN_UNIFIED_MATERIALS:
        raise ValueError(f"至少选 {MIN_UNIFIED_MATERIALS} 支已分析素材")
    if n > MAX_UNIFIED_MATERIALS:
        raise ValueError(f"一次最多 {MAX_UNIFIED_MATERIALS} 支，当前 {n} 支")
    not_done = [m.id for m in materials if not (m.analysis_brief or m.analysis_scenes)]
    if not_done:
        raise ValueError(f"以下素材尚未完成分析：{not_done}")


def estimate_unified_cost(materials: list[Material], our_product: str, model: str) -> dict:
    """干跑：不调 LLM，按粗略 token 估算本次跨素材方向的预估成本（USD）。

    返回 {estimated_cost_usd, model, input_tokens_est, output_tokens_est, material_count}。
    口径偏高（见 rough_token_count）；真实账以调用后 usage 为准。
    """
    _validate_unified_inputs(materials, our_product, model, require_product=False)
    user_msg = _build_unified_user_msg(materials, our_product)
    in_tok = llm_gateway.rough_token_count(_UNIFIED_DIRECTIONS_SYSTEM) + llm_gateway.rough_token_count(user_msg)
    out_tok = _UNIFIED_OUTPUT_TOKENS_EST
    cost = llm_gateway.estimate_cost(
        model, {"prompt_tokens": in_tok, "completion_tokens": out_tok}
    )
    return {
        "estimated_cost_usd": cost.total_usd,
        "model": model,
        "input_tokens_est": in_tok,
        "output_tokens_est": out_tok,
        "material_count": len(materials),
    }


async def generate_unified_directions(
    materials: list[Material], our_product: str, model: str
) -> AdaptResult:
    """选项 C：归纳 N 支已分析素材的共性 → 统一创意方向。

    raises ValueError 当入参不合法（模型/数量/未分析/产品空）。
    输入比单素材大，max_tokens 给足（共性块 + 3-5 方向）。
    """
    _validate_unified_inputs(materials, our_product, model)
    user_msg = _build_unified_user_msg(materials, our_product)
    parsed, cost, used_model = await _call_text_llm(
        _UNIFIED_DIRECTIONS_SYSTEM, user_msg, max_tokens=7000, model=model
    )
    return AdaptResult(data=parsed, cost_usd=cost, model=used_model)
