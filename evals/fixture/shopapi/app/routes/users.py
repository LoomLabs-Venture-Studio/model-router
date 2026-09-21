from fastapi import APIRouter, Depends
from app.core.auth import require_auth
from app.core.users import get_user
router = APIRouter()

@router.get("/users/{user_id}")
def read_user(user_id: int, token=Depends(require_auth)):
    return get_user(user_id)

@router.get("/users/{user_id}/public")
def read_user_public(user_id: int):
    u = get_user(user_id)
    return {"id": u[0]} if u else None
