"""AI 标签分析对话的 schema（P6）。

「AI 导出标签分析」：对当前筛选范围的素材标签 + 已有 AI 分析内容做对话式分析。
两用：一键报告（mode=report，无需提问，对范围出结构化报告）+ 自由追问（mode=chat，
带 message 多轮）。走公司 LLM 网关，零 ST 配额。导出 md/csv 在路由层直出文件。
"""
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field


# ── 一轮分析请求 ──────────────────────────────────────────────────────────

class TagAnalysisRequest(BaseModel):
    """发起一轮分析。session_id 为空 = 新建会话（首轮）；带上 = 在既有会话追问。

    范围参数（app_id/material_type/tag_options）仅新建会话时用于建快照；追问时
    沿用会话已存的范围。mode=report 时 message 可空（用内置报告指令）；mode=chat
    时 message 必填。"""
    session_id: Optional[int] = None
    mode: Literal["report", "chat"] = "report"
    message: Optional[str] = Field(None, max_length=2000)
    model: str  # 网关模型；service 层用白名单校验
    # 新建会话时的范围快照（与素材列表同口径；留空 = 全量）
    app_id: Optional[str] = None
    material_type: Optional[str] = None
    tag_options: Optional[str] = None


# ── 输出 ──────────────────────────────────────────────────────────────────

class TagAnalysisMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    model: Optional[str] = None
    cost_usd: Optional[float] = None
    material_count: Optional[int] = None
    created_at: datetime


class TagAnalysisSessionOut(BaseModel):
    """会话详情（含全部消息）。回查 / 一轮分析后都返回这个完整体。"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    app_id: Optional[str] = None
    material_type: Optional[str] = None
    tag_options: Optional[str] = None
    model: str
    created_at: datetime
    updated_at: datetime
    messages: list[TagAnalysisMessageOut] = []


class TagAnalysisSessionListItem(BaseModel):
    """会话列表项（不含消息正文，轻量）。"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    material_type: Optional[str] = None
    model: str
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class TagAnalysisEstimateOut(BaseModel):
    """单次分析的成本预估（干跑，不真打网关）。用于模型下拉旁展示「约 $X」。

    token 用 rough_token_count 估（CJK 1.3/字、其余 4 字/token，宁高勿低），输出按
    一份结构化报告的典型规模估。empty/over_limit 时不给金额，前端转而提示护栏。"""
    material_count: int
    limit: int
    empty: bool
    over_limit: bool
    model: str
    input_tokens_est: int
    output_tokens_est: int
    estimated_cost_usd: float
