"""Every foreign key resolves in a process that imports only the entry point.

The test suite imports nearly every module, which hid a bug where the API process had no
``app.currencies`` table in its metadata and failed at the first wallet insert. A fresh
interpreter reproduces what uvicorn and the worker really load.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.conftest import BACKEND_DIR

CHECK = """
import importlib, sys
importlib.import_module(sys.argv[1])
from app.core.db import Base
missing = sorted(
    f"{table.name}.{fk.parent.name} -> {fk.target_fullname}"
    for table in Base.metadata.tables.values()
    for fk in table.foreign_keys
    if fk.target_fullname.rsplit(".", 1)[0] not in Base.metadata.tables
)
print("\\n".join(missing))
sys.exit(1 if missing else 0)
"""


@pytest.mark.parametrize("entry_point", ["app.main", "app.worker"])
def test_foreign_key_targets_are_loaded(entry_point: str) -> None:
    result = subprocess.run(  # noqa: S603 - fixed arguments
        [sys.executable, "-c", CHECK, entry_point],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
