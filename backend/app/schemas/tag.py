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
    # 选项级作用域（S2）；空 = 通用。
    app_ids: list[str] = []


class TagOptionUpdate(BaseModel):
    value: Optional[str] = Field(None, min_length=1, max_length=NAME_MAX)
    sort_order: Optional[int] = None
    # None = 不动；[] = 改回通用；非空 = replace-all。
    app_ids: Optional[list[str]] = None


class TagOptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dimension_id: int
    value: str
    sort_order: int
    created_at: datetime
    # 选项级作用域名单（S2）；空 = 通用。
    app_ids: list[str] = []


# ── 一级标签（dimension）──────────────────────────────────────────────────

class TagDimensionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=NAME_MAX)
    value_type: ValueType = "text"
    material_type: Optional[str] = Field(None, max_length=50)
    is_required: bool = False
    allow_multi: bool = True
    sort_order: int = 0
    # 适用产品 app_id 名单（S1）；空 = 通用（所有产品可见）。
    app_ids: list[str] = []


class TagDimensionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=NAME_MAX)
    material_type: Optional[str] = Field(None, max_length=50)
    is_required: Optional[bool] = None
    allow_multi: Optional[bool] = None
    sort_order: Optional[int] = None
    # value_type 刻意不可改：text↔date 切换会让既有二级值 / 已打标记语义错乱，要换重建。
    # None = 不改；[] = 改为通用；非空 = replace-all 重设作用域名单。
    app_ids: Optional[list[str]] = None


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
    # 适用产品名单（S1）；空 = 通用。前端管理页据此渲染「通用 / N 个产品」徽标。
    app_ids: list[str] = []


# ── 产品视角批量改作用域（S4）─────────────────────────────────────────────
# 「产品视角」一屏勾选「限定某产品专属」后，一次原子提交所有改动。
# 每条 = 对某维度/选项的 app_ids 做 replace-all（与单条 PUT 同语义），前端只发改动行。

class TagScopeItem(BaseModel):
    id: int
    app_ids: list[str] = []  # replace-all：[] = 改回通用；非空 = 仅名单内产品


class TagScopeBatchInput(BaseModel):
    dimensions: list[TagScopeItem] = []
    options: list[TagScopeItem] = []


class TagScopeBatchOut(BaseModel):
    updated_dimensions: int
    updated_options: int


class TagReorderInput(BaseModel):
    """重排一级标签顺序：前端传当前显示的完整维度 id 顺序，后端按下标写 sort_order。

    上移/下移/置顶都在前端做本地数组操作，再提交完整顺序——比「相邻交换两个 sort_order」
    稳（现有 sort_order 可能有并列/空洞，交换会撞车）。一次一个原子写、无中间态。
    """
    ordered_ids: list[int]


class TagReorderOutput(BaseModel):
    reordered: int


class TagTemplateCopyInput(BaseModel):
    """以源产品的专属维度为模板，克隆一套给目标产品（新品建标签库场景）。"""
    source_app_id: str = Field(..., min_length=1)
    target_app_id: str = Field(..., min_length=1)
    include_options: bool = True


class TagTemplateCopyOut(BaseModel):
    copied: list[str]      # 新建维度名
    skipped: list[str]     # 目标已有同名可见维度而跳过（幂等）
    options_copied: int


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
