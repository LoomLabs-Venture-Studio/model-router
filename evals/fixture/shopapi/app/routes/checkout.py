import time
from fastapi import APIRouter, Depends
from app.core.auth import require_auth
from app.core.db import query, execute
from app.core.pricing import price_for
from app.core.users import get_user
router = APIRouter()
_inflight = {}

@router.post("/checkout")
def checkout(user_id: int, token=Depends(require_auth)):
    u = get_user(user_id)
    items = query("select sku, qty from cart where user_id = ?", (user_id,))
    total = 0.0
    for sku, qty in items:
        base = query("select price from products where sku = ?", (sku,))[0][0]
        total += price_for(user_id, base) * qty
    _inflight[user_id] = time.time()
    execute("insert into payments(user_id, amount) values (?,?)", (user_id, total))
    execute("delete from cart where user_id = ?", (user_id,))
    del _inflight[user_id]
    return {"total": total}
