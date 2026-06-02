from datetime import datetime, date
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


# ── 素材打标签（material_tag_values，P2）──────────────────────────────────
# 打标签 = 给素材在各一级标签维度下选定值：text 维度选 option(可多)，date 维度选日期。

class MaterialTagValueItem(BaseModel):
    """素材上一条已打标记的对外形态（含维度元信息，免前端再 join）。"""
    dimension_id: int
    dimension_name: str
    value_type: str
    option_id: Optional[int] = None
    value: Optional[str] = None
    value_date: Optional[date] = None


class MaterialTagValueInput(BaseModel):
    """打标签提交：一个维度一条。text 维度给 option_ids（单选时长度≤1），
    date 维度给 value_date。两者按维度 value_type 各取所需。"""
    dimension_id: int
    option_ids: list[int] = []
    value_date: Optional[date] = None


class MaterialTagValuesPut(BaseModel):
    """整体替换某素材的全部结构化标签（replace-all 语义）。"""
    values: list[MaterialTagValueInput] = []


# ── 聚合分析（P4）─────────────────────────────────────────────────────────
# 按某文字型一级标签统计素材分布；可选第二维度做交叉透视。纯本地聚合，零 ST 配额。

class TagAggregateSubBucket(BaseModel):
    """交叉透视时主桶下的次级细分（by 维度每个二级标签的去重素材数）。"""
    option_id: int
    value: str
    count: int


class TagAggregateBucket(BaseModel):
    """主维度某二级标签的桶：命中的去重素材数；交叉透视时带 sub 细分。"""
    option_id: int
    value: str
    count: int
    sub: Optional[list[TagAggregateSubBucket]] = None


class TagAggregateOut(BaseModel):
    dimension_id: int
    dimension_name: str
    by_dimension_id: Optional[int] = None
    by_dimension_name: Optional[str] = None
    total_materials: int   # scope 内去重素材总数
    tagged_materials: int  # scope 内在主维度有任一值的去重素材数（≤ total）
    buckets: list[TagAggregateBucket] = []
