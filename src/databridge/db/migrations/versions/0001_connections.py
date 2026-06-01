"""create connections and sync_jobs tables

Revision ID: 0001
Revises:
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE connections (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_key       TEXT        NOT NULL,
            label           TEXT        NOT NULL,
            type            TEXT        NOT NULL,
            role            TEXT        NOT NULL,
            connection_url  TEXT        NOT NULL,
            credentials_enc BYTEA       NOT NULL,
            status          TEXT        NOT NULL DEFAULT 'untested',
            last_tested_at  TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX connections_owner_key_idx ON connections (owner_key)")

    # Stub table — allows deletion guard in Phase 5 without try/except
    op.execute("""
        CREATE TABLE sync_jobs (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            connection_id  UUID REFERENCES connections(id) ON DELETE RESTRICT
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sync_jobs")
    op.execute("DROP TABLE IF EXISTS connections")
