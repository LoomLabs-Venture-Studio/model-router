"""Task 3: refund logic, POST /billing/{user_id}/refund?payment_id=<int>.

Drives the app through fastapi.testclient.TestClient; verifies effects (the
refunded flag) through a SEPARATE sqlite3 connection to the same shop.db, per
the CTO spec -- never through app internals.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def refunded_flag(payment_id: int) -> int:
    conn = sqlite3.connect("shop.db")
    try:
        row = conn.execute("select refunded from payments where id = ?", (payment_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# Rule 1: valid token required
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_refund_without_any_token_is_401():
    r = client.post("/billing/1/refund", params={"payment_id": 10})
    assert r.status_code == 401


@pytest.mark.target
def test_refund_with_malformed_token_is_401():
    r = client.post("/billing/1/refund", params={"payment_id": 10}, headers=auth("garbage"))
    assert r.status_code == 401


# ----------------------------------------------------------------------------
# Rule 2: caller must be user_id or the admin
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_refund_forbidden_for_a_different_user():
    r = client.post("/billing/1/refund", params={"payment_id": 10}, headers=auth("user-2"))
    assert r.status_code == 403


# ----------------------------------------------------------------------------
# Rule 3: the payment must exist AND belong to user_id
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_refund_unknown_payment_is_404():
    r = client.post("/billing/1/refund", params={"payment_id": 999999}, headers=auth("user-1"))
    assert r.status_code == 404


@pytest.mark.target
def test_refund_payment_belonging_to_someone_else_is_404():
    # payment 10 belongs to user 1; caller is user 2 asking about their own
    # billing (user_id=2 matches the caller, so rule 2 passes) but naming a
    # payment that isn't theirs.
    r = client.post("/billing/2/refund", params={"payment_id": 10}, headers=auth("user-2"))
    assert r.status_code == 404
    assert refunded_flag(10) == 0  # untouched


# ----------------------------------------------------------------------------
# Rule 4: an already-refunded payment is a conflict; nothing changes
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_refunding_an_already_refunded_payment_is_409():
    r = client.post("/billing/1/refund", params={"payment_id": 12}, headers=auth("user-1"))
    assert r.status_code == 409
    assert refunded_flag(12) == 1  # unchanged (was already 1)


# ----------------------------------------------------------------------------
# Check order (shared contract): 401, then 403, then 404, then 409
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_ownership_checked_before_payment_existence():
    # caller doesn't match user_id in the path (403), AND the payment doesn't
    # exist either (404) -- 403 must win per the shared check order.
    r = client.post("/billing/1/refund", params={"payment_id": 999999}, headers=auth("user-2"))
    assert r.status_code == 403


@pytest.mark.target
def test_malformed_token_checked_before_ownership():
    # a malformed token is 401, even on a path that would otherwise be 403.
    r = client.post("/billing/2/refund", params={"payment_id": 10}, headers=auth("whatever"))
    assert r.status_code == 401


# ----------------------------------------------------------------------------
# Guards: everything else keeps working
# ----------------------------------------------------------------------------
@pytest.mark.guard
def test_billing_history_still_returns_the_users_payments():
    r = client.get("/billing/1", headers=auth("user-1"))
    assert r.status_code == 200
    assert "10" in str(r.json()) or 10 in [row[0] for row in r.json()]


@pytest.mark.guard
def test_refund_succeeds_for_the_owner_and_touches_only_that_payment():
    r = client.post("/billing/1/refund", params={"payment_id": 10}, headers=auth("user-1"))
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert refunded_flag(10) == 1
    assert refunded_flag(11) == 0  # someone else's payment: untouched
    assert refunded_flag(12) == 1  # already refunded: unchanged


@pytest.mark.guard
def test_refund_succeeds_for_the_admin():
    r = client.post("/billing/2/refund", params={"payment_id": 11}, headers=auth("admin-token"))
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert refunded_flag(11) == 1
