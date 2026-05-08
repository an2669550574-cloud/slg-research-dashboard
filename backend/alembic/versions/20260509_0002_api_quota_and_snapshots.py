"""api quota + sensor tower snapshots

Revision ID: 0002_quota_snapshots
Revises: 0001_initial
Create Date: 2026-05-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_quota_snapshots"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_quota_monthly",
        sa.Column("year_month", sa.String(7), primary_key=True),  # "2026-05"
        sa.Column("count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "sensor_tower_snapshots",
        sa.Column("cache_key", sa.String(255), primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("sensor_tower_snapshots")
    op.drop_table("api_quota_monthly")
