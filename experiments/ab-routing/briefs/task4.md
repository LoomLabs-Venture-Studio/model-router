# Ticket: fix error status codes

## Context

The project lives at `{PROJECT_DIR}`. Several endpoints currently respond with `200` even when the request doesn't make sense -- an unknown user, a negative quantity, an empty cart at checkout -- sometimes with a `{"error": ...}` body instead of a real error status. Fix the status codes so callers can rely on them. Authentication and ownership are being handled separately; assume every request here is already coming from someone allowed to make it.

## Contract

- Identity: a bearer token `user-<id>` (where `<id>` is a positive integer) identifies user `<id>`. `admin-token` identifies the admin. Any other token, or a missing one, is unauthenticated.
- Status codes: 401 for unauthenticated, 403 for authenticated-but-not-allowed, 404 for an unknown resource, 422 for invalid input, 409 for a conflicting state. Raise these as FastAPI `HTTPException`s (a JSON body with a `detail` field). No endpoint should return 200 with an `{"error": ...}` body.
- When more than one of these rules could apply to a single request, check them in this order: 401, then 403, then 404, then 422, then 409.
- The app locates its database as `shop.db` in the working directory.
- Do not add dependencies. Do not restructure or rename modules, routes, or function signatures beyond what this ticket needs. Work only inside `{PROJECT_DIR}`.
- Use `~/.cache/shopapi-dev/venv/bin/python`, which already has fastapi, httpx, and pytest installed. Run the test suite with `~/.cache/shopapi-dev/venv/bin/python -m pytest`.
- Done means: the rules below hold. Note that the project's two existing tests currently fail because no test database exists; that is a separate ticket, so leave them and the test setup as they are.

## What this ticket needs

1. An unknown user must give 404 on: `GET /users/{user_id}`, `GET /users/{user_id}/public`, `POST /orders` (when its `user_id` doesn't exist), `POST /admin/users/{user_id}/tier` (when `user_id` doesn't exist), and `POST /checkout` (when its `user_id` doesn't exist).
2. `POST /orders` with a `qty` less than 1 must give 422, and no order should be created.
3. `POST /admin/users/{user_id}/tier` with a tier that isn't `free` or `pro` must give 422, and the user's tier must be left unchanged.
4. `POST /checkout` with an empty cart must give 409, and no payment should be created.
5. `POST /checkout` where any cart line names a `sku` that isn't in `products` must give 409, no payment should be created, and the cart must be left unchanged.
6. No endpoint should return 200 with an `{"error": ...}` body.

A valid order must still be created normally, a valid tier change must still work, and a valid checkout must still return the correct total (including the 10% discount for `pro` users), record exactly one payment, and empty the cart.

## Done means

- The six rules above hold.
