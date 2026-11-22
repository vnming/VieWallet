"""Write the OpenAPI document to a file (used to generate the TypeScript API client).

Usage: ``python -m app.scripts.export_openapi ../frontend/packages/api-client/openapi.json``
No database connection is needed: the app is built but its lifespan is not started.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

from app.core.config import Environment, Settings
from app.main import create_app


def build_openapi() -> dict[str, object]:
    settings = Settings(
        environment=Environment.DEVELOPMENT,
        database_url="postgresql+psycopg://unused@localhost/unused",
        # Throwaway keys: building the schema does not encrypt anything.
        data_encryption_key=base64.urlsafe_b64encode(os.urandom(32)).decode(),
        pin_pepper=base64.urlsafe_b64encode(os.urandom(32)).decode(),
        log_json=False,
        log_level="WARNING",
    )
    return create_app(settings).openapi()


def main() -> None:
    if len(sys.argv) != 2:  # noqa: PLR2004
        raise SystemExit("usage: python -m app.scripts.export_openapi <output.json>")
    target = Path(sys.argv[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    document = json.dumps(build_openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    target.write_text(document, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
