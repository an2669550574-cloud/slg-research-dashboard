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


async def _call_text_llm(system: str, user: str, max_tokens: int = 6000) -> tuple[dict, float, str]:
    """纯文本 LLM 调用，返回 (parsed_json, cost, model)。

    max_tokens 默认 6000——方向生成 3-5 条 + checklist 一般 2-3K，
    脚本 20-30 镜头每镜头 ~100 字一般 4-5K；留余量避免截断导致 JSON 不闭合。
    """
    client = llm_gateway.get_client()
    model = settings.TAISHI_VISION_MODEL  # 用同款 sonnet；用户后续可换 glm 省钱
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
