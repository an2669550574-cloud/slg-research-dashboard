"""自有产品画像分析：把「我方产品」挂的素材（宣传片/截图/商店描述）喂给
太石视觉模型，反推产品特点，产出可直接作为创意迁移「自家产品 brief」的成稿。

与 video_analyze 的区别：
- video_analyze 分析**一条买量广告**的钩子/场景/节奏（广告创意层面）。
- 本模块综合**一款产品的多条素材**反推题材/玩法/卖点/受众/差异化（产品画像层面）。

调用路径（routers/product.py 的 /products/{id}/analyze 同步触发）：
    analyze_product(product, materials) →
        视频素材 → video_analyze.extract_frames（少抽几帧）
        图片素材 → PIL 缩放 → jpeg base64
        文字素材 → 直接拼进 prompt
        → 一次多模态调用 → parse JSON → 拼 brief 成稿

成本：本解析**不写 materials 表**，因此不计入 video_analyze.today_cost_usd 聚合；
属低频小额操作（产品 1-2 款、偶尔解析、单次约 $0.01~0.06）。端点侧仍做日预算
前置检查，避免在预算已耗尽的当天继续烧。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
from dataclasses import dataclass
from typing import Optional

from PIL import Image

from app.config import settings
from app.models.product import OwnProduct, OwnProductMaterial
from app.services import llm_gateway, media, video_analyze

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """你是一名资深手机游戏发行 / 买量策划，专长 SLG（策略类）品类。
我会给你「我方某一款产品」的若干素材，可能包含：宣传片 / 买量视频的等间距关键帧、
商店截图、以及商店描述等文字。请综合**所有**素材，反推这款产品的画像，
输出**严格的 JSON**，字段如下（中文回答；不要 markdown、不要任何解释文字）：

{
  "theme": "题材，一句话（如：末日丧尸生存 / 中世纪领主争霸 / 三国乱世）",
  "gameplay": "核心玩法，一句话（如：SLG 城建 + 联盟国战 + 大世界 RTS）",
  "selling_points": ["3~6 个核心卖点短语，每个 ≤ 12 字"],
  "audience": "目标受众画像，一句话（性别/年龄/偏好）",
  "differentiation": "与同类产品的差异化 / 记忆点，一句话",
  "brief": "把以上整合成一段 150~300 字的产品 brief，自然语言、信息密度高，覆盖题材/玩法/卖点/受众/差异化——这段会被直接拿去做创意迁移的『自家产品』输入"
}

输出原则：
- 只输出上述 JSON 对象本体，前后不带 ```、不带任何前后缀文字。
- 基于素材里**能看到 / 读到**的信息推断，不要编造不存在的卖点；信息不足的字段给保守概述即可。
- selling_points 每条简短（≤ 12 字），不要塞整句进去。
"""


@dataclass
class ProductAnalysis:
    brief: str
    theme: Optional[str]
    gameplay: Optional[str]
    selling_points: Optional[list[str]]
    audience: Optional[str]
    differentiation: Optional[str]
    cost_usd: float
    model: str
    material_count: int


def _image_file_to_b64(file_path: str, max_dim: int) -> Optional[str]:
    """读图片素材 → 缩放到最长边 ≤ max_dim → JPEG base64。失败返回 None（跳过该图）。"""
    try:
        path = media.resolve(file_path)
        if not path.is_file():
            return None
        im = Image.open(path).convert("RGB")
        im.thumbnail((max_dim, max_dim), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82, optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        logger.warning("product image decode failed %s: %s", file_path, e)
        return None


def _b64_image_part(b64: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def _build_content(product: OwnProduct, materials: list[OwnProductMaterial]) -> tuple[list[dict], int]:
    """构造多模态 user content。返回 (content, 实际纳入的素材数)。

    在 PRODUCT_ANALYZE_MAX_IMAGES 上限内分配图片额度：先收文字（不占图额度），
    再依次铺视频帧与图片，超额的图片素材只保留其标题文字提示。
    """
    max_images = settings.PRODUCT_ANALYZE_MAX_IMAGES
    content: list[dict] = [
        {"type": "text",
         "text": f"产品名：{product.name}\n以下是这款产品的素材（共 {len(materials)} 条）："},
    ]
    used = 0  # 已纳入素材计数（文字 + 成功取到图的素材）
    img_budget = max_images

    # 先放文字素材（不占图片额度）
    for m in materials:
        if m.asset_type == "text" and (m.text_content or "").strip():
            label = (m.title or "商店描述/文字").strip()
            content.append({"type": "text", "text": f"【文字 · {label}】\n{m.text_content.strip()}"})
            used += 1

    # 再放视频帧与图片
    for m in materials:
        if img_budget <= 0:
            break
        if m.asset_type == "video" and m.file_path:
            path = media.resolve(m.file_path)
            if not path.is_file():
                continue
            n = min(settings.PRODUCT_ANALYZE_FRAMES_PER_VIDEO, img_budget)
            frames = video_analyze.extract_frames(
                path, n, settings.MATERIAL_ANALYZE_FRAME_MAX_DIM
            )
            if not frames:
                continue
            content.append({"type": "text",
                            "text": f"【视频 · {(m.title or m.file_name or '宣传片').strip()}】共 {len(frames)} 帧："})
            for f in frames:
                content.append(_b64_image_part(base64.b64encode(f.jpeg_bytes).decode("ascii")))
            img_budget -= len(frames)
            used += 1
        elif m.asset_type == "image" and m.file_path:
            b64 = _image_file_to_b64(m.file_path, settings.MATERIAL_ANALYZE_FRAME_MAX_DIM)
            if not b64:
                continue
            content.append({"type": "text",
                            "text": f"【图片 · {(m.title or m.file_name or '截图').strip()}】"})
            content.append(_b64_image_part(b64))
            img_budget -= 1
            used += 1

    content.append({"type": "text", "text": "请按 system 指令输出 JSON。"})
    return content, used


def _norm_points(raw) -> Optional[list[str]]:
    if not isinstance(raw, list):
        return None
    out = [str(p).strip() for p in raw if isinstance(p, (str, int)) and str(p).strip()]
    return out[:6] or None


def _compose_brief(parsed: dict) -> str:
    """优先用模型给的整段 brief；缺失则用结构化字段兜底拼一段。"""
    brief = str(parsed.get("brief", "")).strip()
    if brief:
        return brief
    parts = []
    for key, label in (
        ("theme", "题材"), ("gameplay", "玩法"),
        ("audience", "受众"), ("differentiation", "差异化"),
    ):
        v = str(parsed.get(key, "")).strip()
        if v:
            parts.append(f"{label}：{v}")
    pts = _norm_points(parsed.get("selling_points"))
    if pts:
        parts.append("卖点：" + "、".join(pts))
    return "；".join(parts)


async def analyze_product(product: OwnProduct, materials: list[OwnProductMaterial]) -> ProductAnalysis:
    """同步解析（抽帧/图片解码走线程池，避免阻塞事件循环）。

    抛 ValueError（无可用素材 / 解析失败）由 router 转 4xx/5xx。
    """
    if not materials:
        raise ValueError("该产品还没有素材，请先上传宣传片/截图或粘贴商店描述")

    content, used = await asyncio.to_thread(_build_content, product, materials)
    if used == 0:
        raise ValueError("素材都无法解析（文件可能丢失或为空）")

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]
    model = settings.TAISHI_VISION_MODEL
    resp = await llm_gateway.chat_completion(
        model=model, messages=messages, max_tokens=2000, temperature=0.3,
    )
    text = resp.choices[0].message.content or ""
    usage = llm_gateway.usage_to_dict(getattr(resp, "usage", None))
    cost = llm_gateway.estimate_cost(model, usage).total_usd
    parsed = llm_gateway.parse_llm_json(text)  # 共享容错解析（围栏/前后缀/游离引号兜底）

    brief = _compose_brief(parsed)
    if not brief:
        raise ValueError("模型未能从素材里提炼出产品特点，请补充更清晰的素材")

    return ProductAnalysis(
        brief=brief,
        theme=str(parsed.get("theme", "")).strip() or None,
        gameplay=str(parsed.get("gameplay", "")).strip() or None,
        selling_points=_norm_points(parsed.get("selling_points")),
        audience=str(parsed.get("audience", "")).strip() or None,
        differentiation=str(parsed.get("differentiation", "")).strip() or None,
        cost_usd=cost,
        model=model,
        material_count=used,
    )
