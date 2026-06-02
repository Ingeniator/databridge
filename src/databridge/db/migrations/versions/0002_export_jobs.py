"""create export_jobs table

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-02
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE export_jobs (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id              TEXT NOT NULL,
            user_id             TEXT NOT NULL,
            datasource_type     TEXT NOT NULL CHECK (datasource_type IN ('connection', 'system')),
            datasource_ref      TEXT NOT NULL,
            datasource_filter   JSONB NOT NULL DEFAULT '{}',
            datasink_name       TEXT NOT NULL,
            destination_dataset TEXT NOT NULL,
            asset_resolution    BOOLEAN NOT NULL DEFAULT FALSE,
            asset_url_fields    JSONB NOT NULL DEFAULT '[]',
            asset_url_prefix    TEXT NOT NULL DEFAULT '',
            asset_datasink_name TEXT,
            asset_dataset       TEXT,
            status              TEXT NOT NULL DEFAULT 'pending'
                                    CHECK (status IN ('pending', 'running', 'completed', 'failed')),
            records_total       INTEGER,
            records_processed   INTEGER NOT NULL DEFAULT 0,
            records_skipped     INTEGER NOT NULL DEFAULT 0,
            asset_errors        INTEGER NOT NULL DEFAULT 0,
            error_message       TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at          TIMESTAMPTZ,
            completed_at        TIMESTAMPTZ,
            last_heartbeat_at   TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX idx_export_jobs_org_id     ON export_jobs (org_id)")
    op.execute("CREATE INDEX idx_export_jobs_user_id    ON export_jobs (user_id)")
    op.execute("CREATE INDEX idx_export_jobs_status     ON export_jobs (status)")
    op.execute("CREATE INDEX idx_export_jobs_created_at ON export_jobs (created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS export_jobs")
