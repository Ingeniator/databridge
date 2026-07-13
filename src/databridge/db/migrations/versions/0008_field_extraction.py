"""add field extraction columns to export_jobs

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-13
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE export_jobs ADD COLUMN field_extraction BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE export_jobs ADD COLUMN field_extraction_path TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE export_jobs DROP COLUMN IF EXISTS field_extraction_path")
    op.execute("ALTER TABLE export_jobs DROP COLUMN IF EXISTS field_extraction")
