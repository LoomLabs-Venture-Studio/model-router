"""Shared seed data for tasks 2-4's hidden tests (not a test module itself --
leading underscore keeps pytest from collecting it).

Seed data is exactly what PLAYBOOK's Wave 1B task spec fixes: users (1, a@x,
pro), (2, b@x, free); products (sku1, 10.0), (sku2, 2.5); orders (5, user 1,
sku1, 1), (6, user 2, sku2, 3); payments (10, user 1, 9.0, refunded 0),
(11, user 2, 7.5, refunded 0), (12, user 1, 4.0, refunded 1); cart empty
(each test seeds its own cart rows).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SEED_SQL = """
insert into users(id, email, tier) values (1, 'a@x', 'pro'), (2, 'b@x', 'free');
insert into products(sku, price) values ('sku1', 10.0), ('sku2', 2.5);
insert into orders(id, user_id, sku, qty) values (5, 1, 'sku1', 1), (6, 2, 'sku2', 3);
insert into payments(id, user_id, amount, refunded) values
    (10, 1, 9.0, 0), (11, 2, 7.5, 0), (12, 1, 4.0, 1);
"""


def build_shop_db(project_dir: Path) -> Path:
    """(Re)builds shop.db in project_dir from migrations/001_init.sql plus the
    shared seed data. Removes any existing shop.db first. Call this ONCE, before
    the app (and its module-level db connection) is ever imported -- afterwards,
    use reset_seed_data() instead, which never unlinks the file."""
    db_path = project_dir / "shop.db"
    if db_path.exists():
        db_path.unlink()
    migration_sql = (project_dir / "migrations" / "001_init.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(migration_sql)
        conn.executescript(SEED_SQL)
        conn.commit()
    finally:
        conn.close()
    return db_path


def reset_seed_data(db_path: Path) -> None:
    """Resets shop.db's rows back to the shared seed data, via a SEPARATE
    sqlite3 connection to the SAME file -- never app.core.db's own module-level
    connection, and never unlinking/recreating the file (which would orphan
    that already-open connection, pointed at the old inode). Safe to call once
    per test as an autouse fixture."""
    conn = sqlite3.connect(str(db_path))
    try:
        for table in ("cart", "payments", "orders", "products", "users"):
            conn.execute(f"delete from {table}")
        conn.executescript(SEED_SQL)
        conn.commit()
    finally:
        conn.close()
