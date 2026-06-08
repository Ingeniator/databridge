"""add cancelled status to export_jobs

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-04
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old constraint, add new one that includes 'cancelled'
    op.execute("ALTER TABLE export_jobs DROP CONSTRAINT IF EXISTS export_jobs_status_check")
    op.execute(
        "ALTER TABLE export_jobs ADD CONSTRAINT export_jobs_status_check "
        "CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE export_jobs DROP CONSTRAINT IF EXISTS export_jobs_status_check")
    op.execute(
        "ALTER TABLE export_jobs ADD CONSTRAINT export_jobs_status_check "
        "CHECK (status IN ('pending', 'running', 'completed', 'failed'))"
    )
