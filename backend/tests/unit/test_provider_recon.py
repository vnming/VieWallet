"""Provider statement comparison (FT-28): each difference type, matches untouched."""

from __future__ import annotations

import uuid

from app.modules.operations.provider_recon import compare
from app.modules.providers.public import ProviderOperation, StatementLine


def _op(key_: str, amount: int, status: str = "SUCCEEDED") -> ProviderOperation:
    return ProviderOperation(
        id=uuid.UUID(key_),
        operation="CHARGE",
        status=status,
        amount_minor=amount,
        currency="VND",
        provider_reference=f"P-{key_[:4]}",
    )


def _line(key_: str, amount: int, status: str = "SUCCEEDED") -> StatementLine:
    return StatementLine(key_, f"P-{key_[:4]}", "CHARGE", status, amount, "VND")


def test_compare_finds_each_difference_type() -> None:
    keys = [str(uuid.uuid4()) for _ in range(6)]
    internal = {
        keys[0]: _op(keys[0], 1000),
        keys[1]: _op(keys[1], 1000),
        keys[2]: _op(keys[2], 1000, "FAILED"),
        keys[3]: _op(keys[3], 1000),
        keys[5]: _op(keys[5], 1000, "UNKNOWN"),
    }
    lines = [
        _line(keys[0], 1000),
        _line(keys[1], 2000),
        _line(keys[2], 1000),
        _line(keys[4], 500),
        _line(keys[5], 1000, "PENDING"),
    ]
    found = compare(lines, internal, set(internal))
    assert sorted(d.type for d in found) == [
        "AMOUNT_MISMATCH",
        "MISSING_INTERNAL",
        "MISSING_PROVIDER",
        "STATUS_MISMATCH",
    ]
