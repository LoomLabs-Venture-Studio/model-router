# Ticket: authentication and ownership checks

## Context

The project lives at `{PROJECT_DIR}`. Several endpoints currently accept requests from any caller without checking who they are or whether they're allowed to see or change the thing they're asking about. Lock this down. (The refund endpoint, `POST /billing/{user_id}/refund`, is being handled separately -- leave it exactly as it is.)

## Contract

- Identity: a bearer token `user-<id>` (where `<id>` is a positive integer) identifies user `<id>`. `admin-token` identifies the admin. Any other token, or a missing one, is unauthenticated.
- Status codes: 401 for unauthenticated, 403 for authenticated-but-not-allowed, 404 for an unknown resource, 422 for invalid input, 409 for a conflicting state. Raise these as FastAPI `HTTPException`s (a JSON body with a `detail` field). No endpoint should return 200 with an `{"error": ...}` body.
- When more than one of these rules could apply to a single request, check them in this order: 401, then 403, then 404, then 422, then 409.
- The app locates its database as `shop.db` in the working directory.
- Do not add dependencies. Do not restructure or rename modules, routes, or function signatures beyond what this ticket needs. Work only inside `{PROJECT_DIR}`.
- Use `~/.cache/shopapi-dev/venv/bin/python`, which already has fastapi, httpx, and pytest installed. Run the test suite with `~/.cache/shopapi-dev/venv/bin/python -m pytest`.
- Done means: the rules below hold. Note that the project's two existing tests currently fail because no test database exists; that is a separate ticket, so leave them and the test setup as they are.

## What this ticket needs

1. Every endpoint except `GET /health` and `GET /users/{user_id}/public` must require a valid token; otherwise 401. This includes `DELETE /orders/{order_id}`, which currently has no check at all. A malformed bearer token (for example `Bearer garbage` or `Bearer user-abc`) must also be rejected with 401, everywhere this rule applies -- including admin-only endpoints.
2. `GET /users/{user_id}` and `GET /billing/{user_id}`: only that user themselves, or the admin, may call it; otherwise 403.
3. `GET /orders`: a regular user must see only their own orders; the admin sees all of them.
4. `POST /orders`: the `user_id` in the request must be the caller, or the caller must be the admin; otherwise 403.
5. `DELETE /orders/{order_id}`: only the order's owner or the admin may delete it; otherwise 403. If the order doesn't exist, a caller with a valid token gets 404 (ownership can't be checked for an order that isn't there). Without a valid token it is still 401.
6. `POST /checkout`: the `user_id` in the request must be the caller, or the caller must be the admin; otherwise 403.
7. `GET /admin/users` and `POST /admin/users/{user_id}/tier`: admin only; otherwise 403.
8. Leave `POST /billing/{user_id}/refund` exactly as it is; it's out of scope for this ticket.

The happy paths must keep working exactly as they do now: an owner or the admin succeeds with 200 and the same response as before, `GET /health` and the public user endpoint still need no token at all.

## Done means

- The eight rules above hold.
