"""Task 4: error status codes. Every request here uses a token valid under
task 2's rules too (the caller's own user-<id>, or admin-token on admin
endpoints), so these tests pass whether or not ownership is enforced -- this
task is scored on its own, independent of task 2's patch.

Drives the app through fastapi.testclient.TestClient; verifies effects through
a SEPARATE sqlite3 connection to the same shop.db, never app internals.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def db():
    return sqlite3.connect("shop.db")


def order_count() -> int:
    conn = db()
    try:
        return conn.execute("select count(*) from orders").fetchone()[0]
    finally:
        conn.close()


def payment_count() -> int:
    conn = db()
    try:
        return conn.execute("select count(*) from payments").fetchone()[0]
    finally:
        conn.close()


def tier_of(user_id: int) -> str:
    conn = db()
    try:
        return conn.execute("select tier from users where id = ?", (user_id,)).fetchone()[0]
    finally:
        conn.close()


def cart_rows(user_id: int) -> list:
    conn = db()
    try:
        return conn.execute("select sku, qty from cart where user_id = ?", (user_id,)).fetchall()
    finally:
        conn.close()


def seed_cart(rows: list) -> None:
    conn = db()
    try:
        conn.executemany("insert into cart(user_id, sku, qty) values (?, ?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# Rule 1: unknown user -> 404
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_get_user_unknown_is_404():
    r = client.get("/users/999999", headers=auth("admin-token"))
    assert r.status_code == 404


@pytest.mark.target
def test_get_user_public_unknown_is_404():
    r = client.get("/users/999999/public")
    assert r.status_code == 404


@pytest.mark.target
def test_create_order_unknown_user_is_404():
    before = order_count()
    r = client.post("/orders", params={"user_id": 999999, "sku": "sku1", "qty": 1}, headers=auth("admin-token"))
    assert r.status_code == 404
    assert order_count() == before


@pytest.mark.target
def test_set_tier_unknown_user_is_404():
    r = client.post("/admin/users/999999/tier", params={"tier": "pro"}, headers=auth("admin-token"))
    assert r.status_code == 404


@pytest.mark.target
def test_checkout_unknown_user_is_404():
    before = payment_count()
    r = client.post("/checkout", params={"user_id": 999999}, headers=auth("admin-token"))
    assert r.status_code == 404
    assert payment_count() == before


# ----------------------------------------------------------------------------
# Rule 2: POST /orders with qty < 1 -> 422, no order created
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_create_order_qty_zero_is_422():
    before = order_count()
    r = client.post("/orders", params={"user_id": 1, "sku": "sku1", "qty": 0}, headers=auth("user-1"))
    assert r.status_code == 422
    assert order_count() == before


@pytest.mark.target
def test_create_order_negative_qty_is_422():
    before = order_count()
    r = client.post("/orders", params={"user_id": 1, "sku": "sku1", "qty": -1}, headers=auth("user-1"))
    assert r.status_code == 422
    assert order_count() == before


# ----------------------------------------------------------------------------
# Rule 3: POST /admin/users/{id}/tier with an invalid tier -> 422, unchanged
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_set_tier_invalid_value_is_422():
    r = client.post("/admin/users/1/tier", params={"tier": "platinum"}, headers=auth("admin-token"))
    assert r.status_code == 422
    assert tier_of(1) == "pro"  # seed value, unchanged


# ----------------------------------------------------------------------------
# Rule 4: POST /checkout with an empty cart -> 409, no payment created
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_checkout_empty_cart_is_409():
    assert cart_rows(1) == []
    before = payment_count()
    r = client.post("/checkout", params={"user_id": 1}, headers=auth("user-1"))
    assert r.status_code == 409
    assert payment_count() == before


# ----------------------------------------------------------------------------
# Rule 5: POST /checkout with a cart line whose sku isn't in products -> 409
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_checkout_unknown_sku_in_cart_is_409():
    seed_cart([(1, "no-such-sku", 1)])
    before = payment_count()
    r = client.post("/checkout", params={"user_id": 1}, headers=auth("user-1"))
    assert r.status_code == 409
    assert payment_count() == before
    assert cart_rows(1) == [("no-such-sku", 1)]  # cart unchanged


# ----------------------------------------------------------------------------
# Guards: the valid paths keep working, including the pro discount
# ----------------------------------------------------------------------------
@pytest.mark.guard
def test_valid_order_is_created():
    before = order_count()
    r = client.post("/orders", params={"user_id": 2, "sku": "sku2", "qty": 2}, headers=auth("admin-token"))
    assert r.status_code == 200
    assert order_count() == before + 1


@pytest.mark.guard
def test_valid_tier_change_works():
    r = client.post("/admin/users/2/tier", params={"tier": "pro"}, headers=auth("admin-token"))
    assert r.status_code == 200
    assert tier_of(2) == "pro"


@pytest.mark.guard
def test_valid_checkout_applies_pro_discount_and_empties_cart():
    seed_cart([(1, "sku1", 2)])  # user 1 is pro: 10.0 * 0.9 * 2 = 18.0
    before = payment_count()
    r = client.post("/checkout", params={"user_id": 1}, headers=auth("user-1"))
    assert r.status_code == 200
    assert r.json()["total"] == 18.0
    assert payment_count() == before + 1
    assert cart_rows(1) == []
