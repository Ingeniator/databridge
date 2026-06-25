"""add masking, sampling, webhook columns to export_jobs

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-03
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE export_jobs ADD COLUMN masking_rules JSONB NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE export_jobs ADD COLUMN sampling_config JSONB")
    op.execute("ALTER TABLE export_jobs ADD COLUMN webhook_url TEXT")
    op.execute("ALTER TABLE export_jobs ADD COLUMN webhook_enabled BOOLEAN NOT NULL DEFAULT FALSE")


def downgrade() -> None:
    op.execute("ALTER TABLE export_jobs DROP COLUMN IF EXISTS webhook_enabled")
    op.execute("ALTER TABLE export_jobs DROP COLUMN IF EXISTS webhook_url")
    op.execute("ALTER TABLE export_jobs DROP COLUMN IF EXISTS sampling_config")
    op.execute("ALTER TABLE export_jobs DROP COLUMN IF EXISTS masking_rules")
