"""Seeded fee/limit rules read back from the DB, risk assessment log, history pagination."""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.clock import FixedClock
from app.core.db import Database, sqlstate_of
from app.core.money import Money
from app.modules.pricing.public import (
    LimitCode,
    LimitDirection,
    Subject,
    Usage,
    fee_for,
    limit_breach,
)
from app.modules.risk.public import Decision, RiskAssessment, RiskContext, assess
from app.modules.transactions import service as txn_service
from app.modules.transactions.public import (
    SYSTEM,
    AdjustmentDirection,
    AdjustmentReason,
    AdjustmentRequest,
    TxnStatus,
    TxnType,
    adjustment,
)
from app.modules.wallet.public import ensure_wallet
from tests.integration.finance_helpers import make_user
from tests.integration.identity_helpers import signed_in_customer


async def test_seeded_fees_and_limits(db: Database, clock: FixedClock) -> None:
    subject = Subject(uuid.uuid4(), kyc_tier=0)
    async with db.transaction() as session:
        withdrawal = await fee_for(
            session, clock, txn_type="WITHDRAWAL", amount=Money(500_000, "VND"), subject=subject
        )
        transfer = await fee_for(
            session, clock, txn_type="TRANSFER", amount=Money(500_000, "VND"), subject=subject
        )
        none = await fee_for(
            session, clock, txn_type="REFUND", amount=Money(500_000, "VND"), subject=subject
        )
        breach = await limit_breach(
            session,
            txn_type="TRANSFER",
            direction=LimitDirection.OUT,
            amount=Money(3_000_000, "VND"),
            subject=subject,
            usage=Usage(),
        )
        ok = await limit_breach(
            session,
            txn_type="TRANSFER",
            direction=LimitDirection.OUT,
            amount=Money(3_000_000, "VND"),
            subject=Subject(subject.user_id, kyc_tier=1),
            usage=Usage(),
        )
    assert withdrawal.fee == Money(1_100, "VND")
    assert withdrawal.rule_id is not None
    assert transfer.fee.is_zero
    assert none.rule_id is None
    assert breach is not None
    assert breach.code is LimitCode.PER_TXN
    assert ok is None


async def test_risk_assessments_are_recorded_and_immutable(db: Database, clock: FixedClock) -> None:
    user = uuid.uuid4()
    ctx = RiskContext(
        user_id=user,
        txn_type="TRANSFER",
        amount_minor=100_000,
        currency="VND",
        now=clock.now(),
        outgoing_count_10m=15,
        failed_count_30m=4,
    )
    async with db.transaction() as session:
        result = await assess(session, ctx, transaction_id=None)
    assert result.decision is Decision.REVIEW
    async with db.transaction() as session:
        row = await session.scalar(select(RiskAssessment).where(RiskAssessment.user_id == user))
        assert row is not None
        assert row.score == 70
        assert [r["code"] for r in row.matched_rules] == ["VELOCITY_COUNT", "REPEATED_FAILURES"]
    with pytest.raises(DBAPIError) as excinfo:
        async with db.transaction() as session:
            await session.execute(text("UPDATE app.risk_assessments SET score = 0"))
    assert sqlstate_of(excinfo.value) in {"VW001", "42501"}


async def test_history_pagination_filters_and_cas(
    client: httpx.AsyncClient, app: FastAPI, db: Database, clock: FixedClock
) -> None:
    await signed_in_customer(client, app)
    wallet_id = uuid.UUID((await client.get("/api/v1/wallet")).json()["id"])
    async with db.transaction() as session:
        staff = await make_user(session, clock, "Nhân viên")
        created = []
        for i in range(5):
            txn = await txn_service.create(
                session,
                clock,
                txn_type=TxnType.DEPOSIT,
                amount=Money(1_000 * (i + 1), "VND"),
                initiator_user_id=staff,
                actor=SYSTEM,
                destination_wallet_id=wallet_id,
            )
            created.append(txn)
        await txn_service.transition(session, clock, created[0], TxnStatus.PENDING, actor=SYSTEM)

    page1 = (await client.get("/api/v1/transactions?limit=2")).json()
    assert len(page1["items"]) == 2
    assert page1["items"][0]["reference"] == created[-1].reference  # newest first
    page2 = (await client.get(f"/api/v1/transactions?limit=2&cursor={page1['next_cursor']}")).json()
    page3 = (await client.get(f"/api/v1/transactions?limit=2&cursor={page2['next_cursor']}")).json()
    seen = [i["reference"] for p in (page1, page2, page3) for i in p["items"]]
    assert seen == [t.reference for t in reversed(created)]
    assert page3["next_cursor"] is None
    pending = (await client.get("/api/v1/transactions?status=PENDING")).json()
    assert [i["reference"] for i in pending["items"]] == [created[0].reference]
    assert (await client.get("/api/v1/transactions?type=TRANSFER")).json()["items"] == []
    assert (await client.get("/api/v1/transactions/not-a-reference")).status_code == 404

    # Compare-and-set: a stale copy cannot move the transaction, nor can a forbidden edge.
    async with db.transaction() as session:
        stale = await txn_service.get(session, created[1].id)
        fresh = await txn_service.lock(session, created[1].id)
        await txn_service.transition(session, clock, fresh, TxnStatus.CANCELLED, actor=SYSTEM)
        with pytest.raises(txn_service.TxnInvalidStateError):
            await txn_service.transition(session, clock, fresh, TxnStatus.COMPLETED, actor=SYSTEM)
    async with db.transaction() as session:
        stale.status = TxnStatus.CREATED.value
        stale.version = 1
        with pytest.raises(txn_service.TxnConcurrentUpdateError):
            await txn_service.transition(session, clock, stale, TxnStatus.PENDING, actor=SYSTEM)


async def test_wallet_type_constraints(db: Database, clock: FixedClock) -> None:
    async with db.transaction() as session:
        user = await make_user(session, clock)
        wallet = await ensure_wallet(session, clock, user)
        request = AdjustmentRequest(
            wallet, AdjustmentDirection.CREDIT, Money(1, "USD"), AdjustmentReason.GOODWILL, "n"
        )
        with pytest.raises(ValueError, match="currency"):
            await adjustment.propose(
                session, clock, request, maker_id=user, idempotency_key="k" * 10
            )

    async def bad_transfer() -> None:
        # A TRANSFER needs both wallets (CHECK wallets_match_type).
        async with db.transaction() as session:
            user = await make_user(session, clock)
            wallet = await ensure_wallet(session, clock, user)
            await txn_service.create(
                session,
                clock,
                txn_type=TxnType.TRANSFER,
                amount=Money(1, "VND"),
                initiator_user_id=user,
                actor=SYSTEM,
                source_wallet_id=wallet.id,
            )

    with pytest.raises(DBAPIError) as excinfo:
        await bad_transfer()
    assert sqlstate_of(excinfo.value) == "23514"
