"""SQL helpers used by Alembic migrations.

``make_append_only`` is how ledger, event and audit tables become immutable
(database.md §1): a trigger rejects UPDATE/DELETE/TRUNCATE and the application role gets
only SELECT and INSERT on the table.
"""

from __future__ import annotations

import os
import re

from app.core.db import APP_SCHEMA

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def app_role() -> str:
    role = os.environ.get("VW_DB_APP_ROLE", "viewallet_app")
    return _identifier(role)


def _identifier(name: str) -> str:
    if not _IDENTIFIER.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


FORBID_MUTATION_FUNCTION = f"""
CREATE OR REPLACE FUNCTION {APP_SCHEMA}.forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'table %.% is append-only: % is not allowed',
        TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'VW001';
END;
$$;
"""


def make_append_only_sql(table: str) -> list[str]:
    table = _identifier(table)
    qualified = f"{APP_SCHEMA}.{table}"
    return [
        f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {qualified} "
        f"FOR EACH ROW EXECUTE FUNCTION {APP_SCHEMA}.forbid_mutation()",
        f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {qualified} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {APP_SCHEMA}.forbid_mutation()",
        f"REVOKE UPDATE, DELETE, TRUNCATE ON {qualified} FROM {app_role()}",
        f"GRANT SELECT, INSERT ON {qualified} TO {app_role()}",
    ]
