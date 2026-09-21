# Ticket: fix refund logic

## Context

The project lives at `{PROJECT_DIR}`. `POST /billing/{user_id}/refund?payment_id=<id>` currently refunds any payment id, for anyone, with no checks at all -- no auth, no ownership, no check for whether the payment exists or was already refunded. Fix it. Every other endpoint is out of scope for this ticket; leave them exactly as they are.

## Contract

- Identity: a bearer token `user-<id>` (where `<id>` is a positive integer) identifies user `<id>`. `admin-token` identifies the admin. Any other token, or a missing one, is unauthenticated.
- Status codes: 401 for unauthenticated, 403 for authenticated-but-not-allowed, 404 for an unknown resource, 422 for invalid input, 409 for a conflicting state. Raise these as FastAPI `HTTPException`s (a JSON body with a `detail` field). No endpoint should return 200 with an `{"error": ...}` body.
- When more than one of these rules could apply to a single request, check them in this order: 401, then 403, then 404, then 422, then 409.
- The app locates its database as `shop.db` in the working directory.
- Do not add dependencies. Do not restructure or rename modules, routes, or function signatures beyond what this ticket needs. Work only inside `{PROJECT_DIR}`.
- Use `~/.cache/shopapi-dev/venv/bin/python`, which already has fastapi, httpx, and pytest installed. Run the test suite with `~/.cache/shopapi-dev/venv/bin/python -m pytest`.
- Done means: the rules below hold. Note that the project's two existing tests currently fail because no test database exists; that is a separate ticket, so leave them and the test setup as they are.

## What this ticket needs

For `POST /billing/{user_id}/refund?payment_id=<id>`, in this order:

1. A valid token is required; otherwise 401.
2. The caller must be `user_id` themselves, or the admin; otherwise 403.
3. The payment must exist and belong to `user_id`; otherwise 404.
4. A payment that has already been refunded is a conflict: 409, and nothing about it (or anything else) changes.
5. Otherwise, succeed: 200 with body `{"ok": true}`, that payment's `refunded` flag becomes true, and no other payment is affected.
6. Every other endpoint is unchanged and out of scope.

`GET /billing/{user_id}` must keep returning the user's payment history exactly as it does now, and a refund by the payment owner or by the admin must keep succeeding.

## Done means

- The five refund rules above hold, in the order given.
