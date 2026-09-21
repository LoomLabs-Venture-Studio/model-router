from app.core.users import get_user

def test_get_user_missing():
    assert get_user(999999) is None
