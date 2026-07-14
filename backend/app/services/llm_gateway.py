"""太石 LLM 网关封装。

公司统一大模型网关 (relay.tuyoo.com)，OpenAI 兼容协议。直连 Anthropic/OpenAI
在公司合规上不允许；所有大模型调用必须经此中转。

支持模型清单与价目见 memory `reference_taishi_gateway.md` 或 PDF 第 5-15 页。
本模块只负责 client 构造和成本估算；具体业务调用（如视频帧分析）由各
业务 service 自行写 prompt + 解析响应。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from openai import AsyncOpenAI, BadRequestError

from app.config import settings

logger = logging.getLogger(__name__)


# 价目表（USD per 1M tokens）。手册节录的视觉/常用模型；纯 text 模型省略。
# input/output/cache_read/cache_write。未列模型按 sonnet 估（保守，宁高勿低）。
PRICING_USD_PER_1M = {
    "claude-opus-4.5":   {"input": 5.0,  "output": 25.0, "cache_read": 0.5,  "cache_write": 6.25},
    "claude-opus-4.6":   {"input": 5.0,  "output": 25.0, "cache_read": 0.5,  "cache_write": 6.25},
    "claude-opus-4.7":   {"input": 5.0,  "output": 25.0, "cache_read": 0.5,  "cache_write": 6.25},
    "claude-opus-4.8":   {"input": 5.0,  "output": 25.0, "cache_read": 0.5,  "cache_write": 6.25},
    "claude-sonnet-4.5": {"input": 3.0,  "output": 15.0, "cache_read": 0.3,  "cache_write": 3.75},
    "claude-sonnet-4.6": {"input": 3.0,  "output": 15.0, "cache_read": 0.3,  "cache_write": 3.75},
    "gemini-3-flash-preview": {"input": 0.5, "output": 3.0, "cache_read": 0.05, "cache_write": 0.083},
    "glm-4.6": {"input": 0.6, "output": 2.2, "cache_read": 0.11, "cache_write": 0.0},
}
# sonnet-4.5 价目手册没明列，按 sonnet 在 Anthropic 官网历史均价估；如后续手册补全请覆盖。


@dataclass
class CallCost:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_usd: float


def get_client() -> AsyncOpenAI:
    """构造网关 client。每次新建：AsyncOpenAI 本身是轻量包装，连接池在 httpx 层。

    不在模块顶层 cache 是因为 TAISHI_API_KEY 可能在 settings 热重载后变化
    （单元测试 monkeypatch settings 也依赖这点）。
    """
    if not settings.TAISHI_API_KEY:
        raise RuntimeError(
            "TAISHI_API_KEY 未配置——请通过钉钉「运维服务平台→AI 模型申请」"
            "获取 key 后填入 backend/.env"
        )
    return AsyncOpenAI(
        api_key=settings.TAISHI_API_KEY,
        base_url=settings.TAISHI_BASE_URL,
        timeout=settings.TAISHI_TIMEOUT_SECONDS,
    )


async def chat_completion(**kwargs):
    """统一的 chat.completions.create 入口——所有业务调用走这里，别直接拿 client 调。

    新一代 Claude（opus-4.5+ / sonnet-5+）经网关的 Bedrock 后端会对 `temperature`
    硬报 400 ValidationException「temperature is deprecated for this model」（prod
    实锤 2026-07-14 素材分析选 opus-4.7 必炸）。哪些模型拒收随网关后端漂移，白名单
    维护不动——改为撞到该特定 400 时剥掉 temperature 重试一次；接受 temperature
    的模型（sonnet-4.6 / haiku / gemini）行为完全不变。
    """
    try:
        return await get_client().chat.completions.create(**kwargs)
    except BadRequestError as e:
        msg = str(e).lower()
        if "temperature" in kwargs and "temperature" in msg and "deprecated" in msg:
            kwargs.pop("temperature")
            logger.info("gateway rejected temperature for model=%s — retrying without it",
                        kwargs.get("model"))
            return await get_client().chat.completions.create(**kwargs)
        raise


def estimate_cost(model: str, usage: dict) -> CallCost:
    """把 OpenAI 风格的 usage dict 折算成美元成本。

    usage 形如 {"prompt_tokens": N, "completion_tokens": M, ...}。网关如果
    透传 cache 字段（prompt_tokens_details.cached_tokens）也一并计入。
    未知模型按 sonnet 估（保守上限），避免成本计算失败导致整次调用失败。
    """
    price = PRICING_USD_PER_1M.get(model) or PRICING_USD_PER_1M["claude-sonnet-4.5"]
    in_tok = int(usage.get("prompt_tokens", 0) or 0)
    out_tok = int(usage.get("completion_tokens", 0) or 0)
    details = usage.get("prompt_tokens_details") or {}
    cache_read = int(details.get("cached_tokens", 0) or 0)
    # 网关目前没暴露 cache_write 统计；保留字段，后续手册补全再接。
    cache_write = 0
    # cache_read 命中的 token 不再按 input 全价收，从 input 里扣掉。
    billable_input = max(0, in_tok - cache_read)
    cost = (
        billable_input * price["input"] / 1_000_000
        + out_tok * price["output"] / 1_000_000
        + cache_read * price["cache_read"] / 1_000_000
        + cache_write * price["cache_write"] / 1_000_000
    )
    return CallCost(
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        total_usd=round(cost, 6),
    )


def rough_token_count(text: str) -> int:
    """无 tiktoken 的粗略 token 估算，仅供「干跑成本预估」用（不参与真实计费）。

    经验值：CJK 字 ~1.3 token/字，其余（ASCII/标点/空白）~4 字/token。
    宁高勿低——预估偏高让用户看到的金额不低于实际，避免"说好便宜结果更贵"。
    真实账以网关回传 usage 经 estimate_cost 计算为准。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return int(cjk * 1.3 + other / 4 + 0.5)


def usage_to_dict(usage_obj) -> dict:
    """OpenAI SDK 的 usage 可能是 Pydantic model 或 dict（网关回传形式不稳定）。
    统一转 dict 喂给 estimate_cost。"""
    if usage_obj is None:
        return {}
    if isinstance(usage_obj, dict):
        return usage_obj
    if hasattr(usage_obj, "model_dump"):
        return usage_obj.model_dump()
    if hasattr(usage_obj, "to_dict"):
        return usage_obj.to_dict()
    return {}


def _escape_stray_quotes(s: str) -> str:
    """容错修复：转义字符串值内**未转义的英文双引号**——LLM 引用素材文案（如
    `对比"兄弟"和"女友"建的庇护所`）时最常见的坏 JSON 成因。

    只在 json.loads 首次失败后调用（合法 JSON 永不进这里）。启发式：扫描时跟踪
    「是否在字符串内」，串内遇到 `"` 就前看下一个非空白字符——若是结构分隔符
    （`,` `}` `]` `:`）判定为真正的收尾引号；否则判为游离引号、转义成 `\\"`。
    覆盖描述性中文里内嵌引号的主流坏样本；引号紧贴分隔符的歧义样本无法完美还原，
    但作为兜底最坏也只是仍解析失败（不比不修复更糟）。
    """
    out: list[str] = []
    in_str = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if not in_str:
            out.append(c)
            if c == '"':
                in_str = True
            i += 1
            continue
        # 字符串内部
        if c == "\\" and i + 1 < n:            # 已转义序列：整对原样保留
            out.append(c)
            out.append(s[i + 1])
            i += 2
            continue
        if c == '"':
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j >= n or s[j] in ",}]:":       # 后接结构分隔符 → 真正收尾
                out.append('"')
                in_str = False
            else:                              # 游离引号 → 转义
                out.append('\\"')
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_llm_json(text: str) -> dict:
    """从 LLM 响应里稳健地解析出 JSON 对象，容忍常见脏输出：```json``` 围栏、
    前后解释文字、字符串内未转义的英文双引号、以及串内裸控制字符（strict=False）。

    仍无法解析时抛 json.JSONDecodeError（它是 ValueError 子类）——由各 caller 的
    `except json.JSONDecodeError` 兜成 status=failed，保留原始错误信息便于诊断。
    """
    text = text.strip()
    if text.startswith("```"):
        # 剥 ```json ... ``` / ``` ... ```
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    # 兜底：截取第一个 { 到最后一个 } 之间（剥掉前后缀解释文字）
    if not text.startswith("{"):
        s = text.find("{")
        e = text.rfind("}")
        if s >= 0 and e > s:
            text = text[s : e + 1]
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError as first_err:
        # 兜底修复游离引号后再试一次；仍失败则抛**原始**错误（列号对得上原文）
        try:
            return json.loads(_escape_stray_quotes(text), strict=False)
        except json.JSONDecodeError:
            raise first_err
