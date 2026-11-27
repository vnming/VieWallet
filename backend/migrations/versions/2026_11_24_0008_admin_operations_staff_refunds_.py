"""admin operations: staff refunds, versioned pricing, reconciliation items

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-24 06:42:00.572456+00:00

- approval_requests: new action types REFUND, FEE_RULE_CHANGE, LIMIT_RULE_CHANGE, ROLE_CHANGE.
- fee_rules / limit_rules become versioned: unique (code, version), at most one ACTIVE and one
  DRAFT per code, status REJECTED, created_by. The application role may insert drafts and
  update the status/effective columns only (values are immutable; never delete).
- reconciliation_items: resolution note, who/when updated.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.migration_ops import app_role

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIONS_OLD = ("ADJUSTMENT", "WALLET_UNFREEZE", "REVERSAL", "RISK_RULE_CHANGE")
ACTIONS_NEW = (*ACTIONS_OLD, "REFUND", "FEE_RULE_CHANGE", "LIMIT_RULE_CHANGE", "ROLE_CHANGE")
RULE_STATUSES_OLD = ("DRAFT", "ACTIVE", "RETIRED")
RULE_STATUSES_NEW = (*RULE_STATUSES_OLD, "REJECTED")
PRICING = ("fee_rules", "limit_rules")
UPDATABLE = {
    "fee_rules": ("status", "effective_from", "effective_to"),
    "limit_rules": ("status",),
}


def _check(table: str, name: str, column: str, values: tuple[str, ...], *, validate: bool) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, schema="app", type_="check")
    condition = f"{column} IN ({', '.join(repr(v) for v in values)})"
    suffix = "" if validate else " NOT VALID"
    op.execute(
        f"ALTER TABLE app.{table} ADD CONSTRAINT ck_{table}_{name} CHECK ({condition}){suffix}"
    )


def upgrade() -> None:
    _check("approval_requests", "action_valid", "action_type", ACTIONS_NEW, validate=True)
    for table in PRICING:
        _check(table, "status_valid", "status", RULE_STATUSES_NEW, validate=True)
        op.add_column(table, sa.Column("created_by", sa.Uuid(), nullable=True), schema="app")
        op.drop_constraint(op.f(f"uq_{table}_code"), table, schema="app", type_="unique")
        op.create_unique_constraint(
            op.f(f"uq_{table}_code_version"), table, ["code", "version"], schema="app"
        )
        for status in ("ACTIVE", "DRAFT"):
            op.create_index(
                f"uq_{table}_{status.lower()}_code",
                table,
                ["code"],
                unique=True,
                schema="app",
                postgresql_where=sa.text(f"status = '{status}'"),
            )
    op.add_column(
        "reconciliation_items",
        sa.Column("resolution_note", sa.String(length=500), nullable=True),
        schema="app",
    )
    op.add_column(
        "reconciliation_items", sa.Column("updated_by", sa.Uuid(), nullable=True), schema="app"
    )
    op.add_column(
        "reconciliation_items",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        schema="app",
    )
    role = app_role()
    # Drafts are inserted and statuses changed through approval. The values of a version never
    # change (column-level UPDATE only) and nothing is ever deleted.
    for table, columns in UPDATABLE.items():
        op.execute(f"GRANT INSERT, UPDATE ({', '.join(columns)}) ON app.{table} TO {role}")


def downgrade() -> None:
    role = app_role()
    for table, columns in UPDATABLE.items():
        op.execute(f"REVOKE INSERT, UPDATE ({', '.join(columns)}) ON app.{table} FROM {role}")
    op.drop_column("reconciliation_items", "updated_at", schema="app")
    op.drop_column("reconciliation_items", "updated_by", schema="app")
    op.drop_column("reconciliation_items", "resolution_note", schema="app")
    bind = op.get_bind()
    for table in PRICING:
        query = f"SELECT count(*) FROM app.{table} WHERE version > 1"  # noqa: S608 - fixed names
        versions = bind.scalar(sa.text(query))
        if versions:
            raise RuntimeError(
                f"app.{table} holds later rule versions; downgrading would lose rule history"
            )
        for status in ("ACTIVE", "DRAFT"):
            op.drop_index(
                f"uq_{table}_{status.lower()}_code",
                table_name=table,
                schema="app",
                postgresql_where=sa.text(f"status = '{status}'"),
            )
        op.drop_constraint(op.f(f"uq_{table}_code_version"), table, schema="app", type_="unique")
        op.create_unique_constraint(op.f(f"uq_{table}_code"), table, ["code"], schema="app")
        op.drop_column(table, "created_by", schema="app")
        _check(table, "status_valid", "status", RULE_STATUSES_OLD, validate=False)
    _check("approval_requests", "action_valid", "action_type", ACTIONS_OLD, validate=False)
