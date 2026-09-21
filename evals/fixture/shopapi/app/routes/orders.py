from fastapi import APIRouter, Depends
from app.core.auth import require_auth
from app.core.db import query, execute
from app.core.users import get_user
router = APIRouter()

@router.get("/orders")
def list_orders(token=Depends(require_auth)):
    return query("select * from orders")

@router.post("/orders")
def create_order(user_id: int, sku: str, qty: int, token=Depends(require_auth)):
    if not get_user(user_id):
        return {"error": "no user"}
    execute("insert into orders(user_id, sku, qty) values (?,?,?)", (user_id, sku, qty))
    return {"ok": True}

@router.delete("/orders/{order_id}")
def delete_order(order_id: int):
    execute("delete from orders where id = ?", (order_id,))
    return {"ok": True}
