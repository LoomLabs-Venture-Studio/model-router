"""Task 2: authentication and ownership (refund endpoint excluded -- task 3).

Drives the app through fastapi.testclient.TestClient only; never reaches into
app internals. Per-test reset happens via conftest.py's autouse fixture
(a separate sqlite3 connection), so no test here needs to request it by name.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ----------------------------------------------------------------------------
# Rule 1: every endpoint except GET /health and GET /users/{id}/public needs a
# valid token (401 otherwise); a malformed bearer token is also 401.
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_delete_order_without_any_token_is_401():
    r = client.delete("/orders/5")
    assert r.status_code == 401


@pytest.mark.target
def test_malformed_bearer_token_is_401_not_treated_as_a_real_user():
    r = client.get("/users/1", headers=auth("user-abc"))
    assert r.status_code == 401


# ----------------------------------------------------------------------------
# Rule 2: GET /users/{id} and GET /billing/{id} -- only that user or the admin
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_get_user_forbidden_for_a_different_user():
    r = client.get("/users/2", headers=auth("user-1"))
    assert r.status_code == 403


@pytest.mark.target
def test_get_billing_forbidden_for_a_different_user():
    r = client.get("/billing/2", headers=auth("user-1"))
    assert r.status_code == 403


# ----------------------------------------------------------------------------
# Rule 3: GET /orders -- a user sees only their own; the admin sees all
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_list_orders_shows_only_the_caller_own_orders():
    r = client.get("/orders", headers=auth("user-1"))
    assert r.status_code == 200
    body = str(r.json())
    assert "sku1" in body  # order 5: user 1's own order
    assert "sku2" not in body  # order 6 belongs to user 2


# ----------------------------------------------------------------------------
# Rule 4: POST /orders -- user_id must be the caller, or the caller is admin
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_create_order_forbidden_when_user_id_does_not_match_caller():
    r = client.post("/orders", params={"user_id": 2, "sku": "sku1", "qty": 1}, headers=auth("user-1"))
    assert r.status_code == 403


# ----------------------------------------------------------------------------
# Rule 5: DELETE /orders/{id} -- only the order's owner or the admin; unknown -> 404
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_delete_order_forbidden_for_a_non_owner():
    r = client.delete("/orders/5", headers=auth("user-2"))
    assert r.status_code == 403


@pytest.mark.target
def test_delete_unknown_order_is_404():
    r = client.delete("/orders/999999", headers=auth("admin-token"))
    assert r.status_code == 404


# ----------------------------------------------------------------------------
# Rule 6: POST /checkout -- user_id must be the caller, or the caller is admin
# ----------------------------------------------------------------------------
@pytest.mark.target
def test_checkout_forbidden_when_user_id_does_not_match_caller():
    r = client.post("/checkout", params={"user_id": 2}, headers=auth("user-1"))
    assert r.status_code == 403


# ----------------------------------------------------------------------------
# Rule 7: GET /admin/users and POST /admin/users/{id}/tier -- admin only
# ----------------------------------------------------------------------------
@pytest.mark.guard
def test_admin_users_list_forbidden_for_a_regular_user():
    r = client.get("/admin/users", headers=auth("user-1"))
    assert r.status_code == 403


@pytest.mark.target
def test_set_tier_forbidden_for_a_regular_user():
    r = client.post("/admin/users/1/tier", params={"tier": "pro"}, headers=auth("user-2"))
    assert r.status_code == 403


# ----------------------------------------------------------------------------
# Guards: the excluded endpoints, and the happy paths, must keep working
# ----------------------------------------------------------------------------
@pytest.mark.guard
def test_health_needs_no_token():
    assert client.get("/health").status_code == 200


@pytest.mark.guard
def test_public_user_endpoint_needs_no_token():
    r = client.get("/users/1/public")
    assert r.status_code == 200
    assert "1" in str(r.json())


@pytest.mark.guard
def test_owner_can_view_own_profile():
    r = client.get("/users/1", headers=auth("user-1"))
    assert r.status_code == 200


@pytest.mark.guard
def test_admin_can_view_any_profile():
    r = client.get("/users/2", headers=auth("admin-token"))
    assert r.status_code == 200


@pytest.mark.guard
def test_owner_can_delete_own_order():
    r = client.delete("/orders/5", headers=auth("user-1"))
    assert r.status_code == 200


@pytest.mark.guard
def test_admin_can_list_all_admin_users():
    r = client.get("/admin/users", headers=auth("admin-token"))
    assert r.status_code == 200
