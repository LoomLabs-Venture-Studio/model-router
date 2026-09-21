# Ticket: make the shopapi test suite runnable on a clean checkout

## Context

The project lives at `{PROJECT_DIR}`. Right now, `pytest` fails on a fresh checkout because no `shop.db` exists yet, and the two existing tests (`tests/test_users.py::test_get_user_missing` and `tests/test_pricing.py::test_price_for_unknown_user`) error out instead of running. Make the test suite self-contained so it works out of the box, without changing how the app behaves outside of tests.

## Contract

- Identity: a bearer token `user-<id>` (where `<id>` is a positive integer) identifies user `<id>`. `admin-token` identifies the admin. Any other token, or a missing one, is unauthenticated.
- Status codes: 401 for unauthenticated, 403 for authenticated-but-not-allowed, 404 for an unknown resource, 422 for invalid input, 409 for a conflicting state. Raise these as FastAPI `HTTPException`s (a JSON body with a `detail` field). No endpoint should return 200 with an `{"error": ...}` body.
- When more than one of these rules could apply to a single request, check them in this order: 401, then 403, then 404, then 422, then 409.
- The app locates its database as `shop.db` in the working directory.
- Do not add dependencies. Do not restructure or rename modules, routes, or function signatures beyond what this ticket needs. Work only inside `{PROJECT_DIR}`.
- Use `~/.cache/shopapi-dev/venv/bin/python`, which already has fastapi, httpx, and pytest installed. Run the test suite with `~/.cache/shopapi-dev/venv/bin/python -m pytest`.
- Done means: the rules below hold, and the project's own test suite passes.

## What this ticket needs

1. On a clean checkout with no `shop.db` present, running the test suite must exit 0, and the two existing tests must still exist and pass.
2. Tests must get a database built from `migrations/001_init.sql`, in a temporary location -- never the project's real `shop.db`.
3. Isolation must be automatic: a test that writes rows must not affect any other test, regardless of run order, and no test should have to ask for a fixture by name to get a clean database.
4. Running the test suite must never change the contents of a `shop.db` that already exists in the project directory.
5. Outside of the tests, nothing changes: the app must keep using `shop.db` in the working directory by default.

## Done means

- The five rules above hold.
- The project's own test suite passes.
