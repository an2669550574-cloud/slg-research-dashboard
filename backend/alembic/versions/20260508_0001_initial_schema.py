"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 注意：Column(unique=True, index=True) 会让 SQLAlchemy 在 create_table 时
    # 自动创建相应的 UNIQUE INDEX，无需再 op.create_index() 否则会重复。
    op.create_table(
        "games",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("app_id", sa.String(100), nullable=False, unique=True, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("publisher", sa.String(200), nullable=True),
        sa.Column("icon_url", sa.String(500), nullable=True),
        sa.Column("category", sa.String(100), nullable=False, server_default="SLG"),
        sa.Column("platform", sa.String(20), nullable=False, server_default="ios"),
        sa.Column("country", sa.String(10), nullable=False, server_default="US"),
        sa.Column("release_date", sa.String(20), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("tags", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "game_rankings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("app_id", sa.String(100), nullable=False, index=True),
        sa.Column("date", sa.String(20), nullable=False, index=True),
        sa.Column("rank", sa.Integer, nullable=True),
        sa.Column("downloads", sa.Float, nullable=True),
        sa.Column("revenue", sa.Float, nullable=True),
        sa.Column("country", sa.String(10), nullable=False, server_default="US"),
        sa.Column("platform", sa.String(20), nullable=False, server_default="ios"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "game_histories",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("app_id", sa.String(100), nullable=False, index=True),
        sa.Column("event_date", sa.String(20), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "materials",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("app_id", sa.String(100), nullable=False, index=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("platform", sa.String(50), nullable=True),
        sa.Column("material_type", sa.String(50), nullable=False, server_default="video"),
        sa.Column("tags", sa.JSON, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    # SQLAlchemy 在 drop_table 时会一并清掉 column-level 自动索引
    op.drop_table("materials")
    op.drop_table("game_histories")
    op.drop_table("game_rankings")
    op.drop_table("games")
