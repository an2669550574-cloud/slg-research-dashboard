from sqlalchemy import String, Integer, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.database import Base, utcnow_naive


class OwnProduct(Base):
    """自家产品档案。创意迁移的「自家产品 brief」从这里取，免去每次手输。

    brief 是自由文本（题材 / 玩法 / 卖点 / 受众 / 差异化），与 LLM 服务层
    现有入参格式一致——前端选中后填进文本框、可临时改、仍以纯文本发给后端。
    """
    __tablename__ = "own_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    brief: Mapped[str] = mapped_column(Text)
    # 创意迁移面板打开时默认带入这条。全表至多一条为 True（写入时互斥）。
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )
