from fastapi import APIRouter, Depends
from app.core.auth import require_auth, require_admin
from app.core.db import query, execute
from app.core.users import get_user
router = APIRouter()

@router.get("/admin/users")
def all_users(token=Depends(require_auth)):
    require_admin(token)
    return query("select * from users")

@router.post("/admin/users/{user_id}/tier")
def set_tier(user_id: int, tier: str, token=Depends(require_auth)):
    if not get_user(user_id):
        return {"error": "no user"}
    execute("update users set tier = ? where id = ?", (tier, user_id))
    return {"ok": True}
