"""publisher_itunes_artists + publisher_itunes_apps: App Store 开发者账号与 app 清单快照

Revision ID: 0016_publisher_itunes_apps
Revises: 0015_publisher_relations
Create Date: 2026-06-10

「厂商新品 P2」：主体挂 iTunes artistId（开发者账号，一对多），周级用免费 iTunes
lookup API 拉账号下全部 app 清单做 diff——新 track_id = 新上架，不依赖产品进榜。
首次同步标 is_baseline=True 不报新。纯新增两表，向前兼容、回滚走纯代码。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016_publisher_itunes_apps"
down_revision: Union[str, None] = "0015_publisher_relations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "publisher_itunes_artists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entity_id", sa.Integer(),
            sa.ForeignKey("publisher_entities.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("artist_id", sa.String(length=30), nullable=False, unique=True),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_publisher_itunes_artists_entity_id", "publisher_itunes_artists", ["entity_id"]
    )

    op.create_table(
        "publisher_itunes_apps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entity_id", sa.Integer(),
            sa.ForeignKey("publisher_entities.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "artist_row_id", sa.Integer(),
            sa.ForeignKey("publisher_itunes_artists.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("track_id", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("bundle_id", sa.String(length=200), nullable=True),
        sa.Column("release_date", sa.String(length=30), nullable=True),
        sa.Column("track_view_url", sa.String(length=1000), nullable=True),
        sa.Column("is_baseline", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_seen_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("artist_row_id", "track_id", name="uq_itunes_app_per_artist"),
    )
    op.create_index("ix_publisher_itunes_apps_entity_id", "publisher_itunes_apps", ["entity_id"])
    op.create_index(
        "ix_publisher_itunes_apps_artist_row_id", "publisher_itunes_apps", ["artist_row_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_publisher_itunes_apps_artist_row_id", table_name="publisher_itunes_apps")
    op.drop_index("ix_publisher_itunes_apps_entity_id", table_name="publisher_itunes_apps")
    op.drop_table("publisher_itunes_apps")
    op.drop_index("ix_publisher_itunes_artists_entity_id", table_name="publisher_itunes_artists")
    op.drop_table("publisher_itunes_artists")
