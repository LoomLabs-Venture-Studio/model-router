from app.core.pricing import price_for

def test_price_for_unknown_user():
    assert price_for(999999, 10.0) == 10.0
