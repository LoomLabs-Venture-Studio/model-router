from app.core.users import get_user

def price_for(user_id: int, base: float) -> float:
    u = get_user(user_id)
    if u and u[2] == "pro":
        return round(base * 0.9, 2)
    return base
