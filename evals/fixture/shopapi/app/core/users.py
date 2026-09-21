from app.core.db import query

def get_user(user_id: int):
    rows = query("select id, email, tier from users where id = ?", (user_id,))
    return rows[0] if rows else None
