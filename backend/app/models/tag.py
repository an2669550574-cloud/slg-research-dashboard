from sqlalchemy import String, Integer, DateTime, Date, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, date
from typing import Optional
from app.database import Base, utcnow_naive


class TagDimension(Base):
    """一级标签 / 框架（migration 0011）。「标签库」的骨架。

    value_type 决定二级标签的形态：
    - 'text' → 二级是受控枚举值，维护在 TagOption 表，打标签时下拉选
    - 'date' → 二级是「选个日期」，无预设 TagOption，打标签时存日期（见 MaterialTagValue.value_date）
    名称提交时限 8 个字符（前端不限输入过程，防 IME 拼音打不进；见 schema 校验）。
    删除一级标签会连带其二级标签 + 已打标记一并移除（应用层显式级联，SQLite 不强制 FK）。
    """
    __tablename__ = "tag_dimensions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))  # 中文展示名，如「路型」「投放时间」
    value_type: Mapped[str] = mapped_column(String(10), default="text")  # text / date
    # 适用素材类型 video/image/playable；None=全部（需求「按素材类型设置」）
    material_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)  # 上传时必选
    allow_multi: Mapped[bool] = mapped_column(Boolean, default=True)   # 框架内可多选
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class TagOption(Base):
    """二级标签（migration 0011）。仅 value_type='text' 的一级标签下维护枚举值。"""
    __tablename__ = "tag_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dimension_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tag_dimensions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    value: Mapped[str] = mapped_column(String(50))  # 标签值，如「3路」「红桶」
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class MaterialTagValue(Base):
    """素材 ↔ 标签值的关联（junction，migration 0011）。结构化打标签用，与扁平
    materials.tags / analysis_tags 并存不冲突。

    - text 维度：option_id 指向 TagOption，value 冗余存实际值（聚合 GROUP BY value 免 join）
    - date 维度：option_id 为空，value_date 存所选日期（范围筛选 / 排序 / 自动命名用）
    option 改名时由接口同步刷新本表 value；素材 / 维度 / 选项删除时应用层显式级联。
    """
    __tablename__ = "material_tag_values"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("materials.id", ondelete="CASCADE"), index=True, nullable=False
    )
    dimension_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tag_dimensions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    option_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("tag_options.id", ondelete="CASCADE"), nullable=True
    )
    value: Mapped[Optional[str]] = mapped_column(String(50), index=True, nullable=True)
    value_date: Mapped[Optional[date]] = mapped_column(Date, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
