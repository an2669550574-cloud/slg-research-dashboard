from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field

# 一级标签名上限：8 个字符。提交时校验（前端不限输入过程，防 IME 拼音打不进）。
NAME_MAX = 8

ValueType = Literal["text", "date"]


# ── 二级标签（option）─────────────────────────────────────────────────────

class TagOptionCreate(BaseModel):
    value: str = Field(..., min_length=1, max_length=NAME_MAX)
    sort_order: int = 0


class TagOptionUpdate(BaseModel):
    value: Optional[str] = Field(None, min_length=1, max_length=NAME_MAX)
    sort_order: Optional[int] = None


class TagOptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dimension_id: int
    value: str
    sort_order: int
    created_at: datetime


# ── 一级标签（dimension）──────────────────────────────────────────────────

class TagDimensionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=NAME_MAX)
    value_type: ValueType = "text"
    material_type: Optional[str] = Field(None, max_length=50)
    is_required: bool = False
    allow_multi: bool = True
    sort_order: int = 0


class TagDimensionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=NAME_MAX)
    material_type: Optional[str] = Field(None, max_length=50)
    is_required: Optional[bool] = None
    allow_multi: Optional[bool] = None
    sort_order: Optional[int] = None
    # value_type 刻意不可改：text↔date 切换会让既有二级值 / 已打标记语义错乱，要换重建。


class TagDimensionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    value_type: str
    material_type: Optional[str] = None
    is_required: bool
    allow_multi: bool
    sort_order: int
    created_at: datetime
    options: list[TagOptionOut] = []
