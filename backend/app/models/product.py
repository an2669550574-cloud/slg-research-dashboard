from sqlalchemy import String, Integer, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional
from app.database import Base, utcnow_naive


class OwnProduct(Base):
    """自家产品档案。创意迁移的「自家产品 brief」从这里取，免去每次手输。

    brief 是自由文本（题材 / 玩法 / 卖点 / 受众 / 差异化），与 LLM 服务层
    现有入参格式一致——前端选中后填进文本框、可临时改、仍以纯文本发给后端。
    可挂自有产品素材（OwnProductMaterial），AI 据此反推产品特点生成 brief 草稿。
    """
    __tablename__ = "own_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    brief: Mapped[str] = mapped_column(Text)
    # 「同赛道」匹配关键词：逗号分隔的题材/玩法词（如「丧尸,末日,survival,zombie」）。digest 用它
    # 对竞品名 + LLM 中文摘要做纯本地小写子串匹配。**题材关键词先天太宽泛**（末日横跨多品类），
    # 优先用 match_subgenre 精确匹配；本字段作回退/兼容。空/None = 不参与关键词匹配。
    match_keywords: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # 「同赛道」目标玩法子品类（受控词表，与 market_newcomer_log.subgenre_cn 同口径，如「数字门SLG」）。
    # 配了就**优先按子品类相等精确匹配**（竞品的 LLM 分类 subgenre_cn == 本值）——治题材关键词
    # 分不出「数字门 SLG vs 基地建设 SLG」。逗号分隔可配多个子品类。空 = 退回 match_keywords。
    match_subgenre: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # 创意迁移面板打开时默认带入这条。全表至多一条为 True（写入时互斥）。
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )


class OwnProductMaterial(Base):
    """自有产品的素材：宣传片 / 商店截图 / 商店描述等，用来喂给 AI 反推产品画像。

    与竞品素材库（Material，强绑 app_id）**刻意隔离**——这里的素材属于自家产品、
    用途是「解析产品特点生成 brief」，而非竞品创意调研。三种形态：
    - video / image：上传文件，复用 MEDIA_ROOT 存储（file_path 等字段）
    - text：直接粘贴的商店描述 / 介绍文字（text_content）

    删除产品时由 router 手动级联删除子素材 + 落盘文件（SQLite 默认不强制 FK 级联，
    且删文件这类副作用本就该在应用层做）。
    """
    __tablename__ = "own_product_materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    own_product_id: Mapped[int] = mapped_column(
        ForeignKey("own_products.id", ondelete="CASCADE"), index=True
    )
    asset_type: Mapped[str] = mapped_column(String(20))  # video / image / text
    title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # upload 形态（video/image）
    file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # MEDIA_ROOT 下相对路径
    file_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # text 形态
    text_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
