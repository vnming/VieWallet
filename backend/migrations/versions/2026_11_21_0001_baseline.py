"""Baseline: app schema grants, append-only trigger function, shared-kernel tables.

Revision ID: 0001
Revises:
Create Date: 2026-11-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.migration_ops import FORBID_MUTATION_FUNCTION, app_role

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "app"


def upgrade() -> None:
    role = app_role()
    # Least privilege for the application role (database.md §1).
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {role}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} GRANT USAGE, SELECT ON SEQUENCES TO {role}"
    )
    op.execute(f"GRANT SELECT ON {SCHEMA}.alembic_version TO {role}")
    op.execute(FORBID_MUTATION_FUNCTION)

    op.create_table(
        "currencies",
        sa.Column("code", sa.String(3), nullable=False),
        sa.Column("exponent", sa.SmallInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("code ~ '^[A-Z]{3}$'", name="ck_currencies_code_format"),
        sa.CheckConstraint("exponent BETWEEN 0 AND 4", name="ck_currencies_exponent_range"),
        sa.PrimaryKeyConstraint("code", name="pk_currencies"),
        schema=SCHEMA,
    )
    # Reference data is managed by the migrator (seed), read-only for the application.
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON {SCHEMA}.currencies FROM {role}")

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(100), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED')", name="ck_idempotency_keys_status_valid"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_keys"),
        sa.UniqueConstraint(
            "principal_id", "scope", "key", name="uq_idempotency_keys_principal_id_scope_key"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"], schema=SCHEMA
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("aggregate_type", sa.String(50), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'DONE', 'DEAD')",
            name="ck_outbox_events_status_valid",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_events_attempts_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_outbox_events_due",
        "outbox_events",
        ["next_attempt_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('PENDING', 'PROCESSING')"),
    )

    op.create_table(
        "scheduled_jobs",
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(20), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "last_status IS NULL OR last_status IN ('RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_scheduled_jobs_last_status_valid",
        ),
        sa.PrimaryKeyConstraint("name", name="pk_scheduled_jobs"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    role = app_role()
    op.drop_table("scheduled_jobs", schema=SCHEMA)
    op.drop_index("ix_outbox_events_due", table_name="outbox_events", schema=SCHEMA)
    op.drop_table("outbox_events", schema=SCHEMA)
    op.drop_index("ix_idempotency_keys_expires_at", table_name="idempotency_keys", schema=SCHEMA)
    op.drop_table("idempotency_keys", schema=SCHEMA)
    op.drop_table("currencies", schema=SCHEMA)
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.forbid_mutation()")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {role}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} REVOKE USAGE, SELECT ON SEQUENCES FROM {role}"  # noqa: S608 - role is a validated identifier
    )
