"""Seed script, OpenAPI export and worker wiring."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import select, text

from app.core.clock import FixedClock
from app.core.config import Settings
from app.core.db import Database
from app.core.idempotency import IdempotencyStore, OperationResult
from app.core.jobs import JobScheduler
from app.core.models import CurrencyRecord
from app.scripts import export_openapi
from app.scripts.seed import seed
from app.worker import build_jobs, build_registry, run_worker


async def test_seed_is_idempotent(migrator_db: Database, db: Database) -> None:
    await seed(migrator_db)
    await seed(migrator_db)
    async with db.transaction() as session:
        rows = (await session.scalars(select(CurrencyRecord).order_by(CurrencyRecord.code))).all()
    assert [(r.code, r.exponent, r.enabled) for r in rows] == [
        ("USD", 2, False),
        ("VND", 0, True),
    ]


async def test_export_openapi_writes_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "openapi.json"
    monkeypatch.setattr(sys, "argv", ["export_openapi", str(target)])
    export_openapi.main()
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["info"]["title"] == "VieWallet API"
    assert "/api/v1/system/status" in document["paths"]


async def test_export_openapi_requires_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["export_openapi"])
    with pytest.raises(SystemExit):
        export_openapi.main()


async def test_purge_job_removes_expired_keys(
    db: Database, clock: FixedClock, app_settings: Settings, app: FastAPI
) -> None:
    store = IdempotencyStore(clock)

    async def noop(_session: object) -> OperationResult:
        return OperationResult(200, {})

    await store.execute(
        db, principal_id="p", scope="s", key="expired-key-1", payload={}, operation=noop
    )
    clock.advance(timedelta(hours=25))
    jobs = build_jobs(db, clock, app_settings, app.state.finance)
    names = [j.name for j in jobs]
    assert names == [
        "purge_expired_idempotency_keys",
        "internal_reconciliation",
        "expire_approval_requests",
        "provider_status_inquiries",
        "expire_withdrawal_quotes",
        "resubmit_stalled_deposits",
        "expire_spend_quotes",
        "expire_payment_requests",
        "expire_risk_holds",
        "expire_kyc_approvals",
        "transaction_exports",
    ]
    assert await JobScheduler(db, clock, jobs).run_due() == names
    async with db.transaction() as session:
        assert await session.scalar(text("SELECT count(*) FROM app.idempotency_keys")) == 0


async def test_worker_starts_and_stops_cleanly(app_settings: Settings, app: FastAPI) -> None:
    assert build_registry(app_settings, app.state.finance).topics == [
        "identity.pin_locked",
        "kyc.status_changed",
        "notifications.email.requested",
        "provider.callback_received",
        "reconciliation.violation_found",
        "support.ticket_updated",
        "transaction.recovery_needed",
        "transaction.status_changed",
        "withdrawal.dispatch_requested",
    ]
    stop = asyncio.Event()
    task = asyncio.create_task(run_worker(app_settings, stop))
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
