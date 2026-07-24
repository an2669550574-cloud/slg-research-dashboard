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


class TagOptionProduct(Base):
    """二级标签 ↔ 产品(app_id) 作用域（migration 0025，S2）。

    空名单 = 通用选项；非空 = 仅名单内产品可见（典型：「角色」维度共享、各游戏角色值不混）。
    打标签时与维度作用域并列生效：先按维度名单收敛 dim 列表，再按选项名单收敛 dim.options。
    删二级标签时由 FK ondelete=CASCADE 自动清理。
    """
    __tablename__ = "tag_option_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    option_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tag_options.id", ondelete="CASCADE"), index=True, nullable=False
    )
    app_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class TagDimensionProduct(Base):
    """一级标签 ↔ 产品(app_id) 作用域（migration 0024，S1）。

    空名单 = 通用维度（所有产品可见，= 现有 7 个种子维度的现状）；非空 = 只对名单内产品可见。
    打标签 / 维度列表过滤时按 `无名单 OR 名单含目标 app_id` 取并集。
    删一级标签时由 FK ondelete=CASCADE 自动清理本表。
    """
    __tablename__ = "tag_dimension_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dimension_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tag_dimensions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    app_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class TagPack(Base):
    """标签包（migration 0046）：把一级标签分组成自定义大类（如「物资链路」「投放要点」）。

    包是**视图不是分区**：一个维度可同属多个包（多对多，见 TagPackDimension）。
    素材库启用包视图的产品（TagPackSetting.enabled）可按包切换分面；不建包 / 开关关 =
    行为与无此功能时完全一致。名称提交时限 20 字符（比维度的 8 字宽松，包名允许更长）。
    """
    __tablename__ = "tag_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))  # 中文展示名，如「物资链路」
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class TagPackDimension(Base):
    """标签包 ↔ 一级标签 多对多（migration 0046）。

    删包由 FK ondelete=CASCADE 清理；删维度时应用层显式连带清理本表
    （与 delete_dimension 既有套路一致，SQLite 不强制 FK）。
    """
    __tablename__ = "tag_pack_dimensions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pack_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tag_packs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    dimension_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tag_dimensions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class TagPackOption(Base):
    """标签包 ↔ 二级标签 选项子集成员（migration 0047）。

    与 TagPackDimension（整维度成员）互斥并存：整维度 = 全部选项 + 新增选项自动进包；
    选项子集 = 固定名单。同包同维度两种形态互斥，API 写入时归一（整维度优先）。
    删包 FK CASCADE；删选项/删维度时应用层显式连带清理（SQLite 不强制 FK）。
    """
    __tablename__ = "tag_pack_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pack_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tag_packs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    option_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tag_options.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class TagPackProduct(Base):
    """标签包 ↔ 产品(app_id) 作用域（migration 0046）。

    沿用 0024/0025 范式：空名单 = 通用包（所有产品可见）；非空 = 仅名单内产品可见。
    """
    __tablename__ = "tag_pack_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pack_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tag_packs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    app_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class TagPackSetting(Base):
    """产品级标签包总开关（migration 0046）。

    素材库是否对该产品启用「按包筛选」视图。**无记录 = 默认关**（新功能对所有产品
    静默，逐产品手动开启）——所以只存显式设置过的行，别为全量产品预建。
    """
    __tablename__ = "tag_pack_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
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
