"""add webhook_payload_template column to export_jobs

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-19
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE export_jobs ADD COLUMN webhook_payload_template TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE export_jobs DROP COLUMN IF EXISTS webhook_payload_template")
