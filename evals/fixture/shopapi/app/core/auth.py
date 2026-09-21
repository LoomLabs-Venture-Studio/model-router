from fastapi import Header, HTTPException

def require_auth(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing token")
    return authorization.split(" ", 1)[1]

def require_admin(token: str):
    if token != "admin-token":
        raise HTTPException(403, "admin only")
    return token
