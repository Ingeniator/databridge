"""add external_asset_dataset_id column to export_jobs

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-20
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE export_jobs ADD COLUMN external_asset_dataset_id TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE export_jobs DROP COLUMN IF EXISTS external_asset_dataset_id")
