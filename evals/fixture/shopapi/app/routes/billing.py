from fastapi import APIRouter, Depends
from app.core.auth import require_auth
from app.core.db import query, execute
router = APIRouter()

@router.get("/billing/{user_id}")
def billing_history(user_id: int, token=Depends(require_auth)):
    return query("select * from payments where user_id = ?", (user_id,))

@router.post("/billing/{user_id}/refund")
def refund(user_id: int, payment_id: int):
    execute("update payments set refunded = 1 where id = ?", (payment_id,))
    return {"ok": True}
