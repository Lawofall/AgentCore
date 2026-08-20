from shelfstock.catalog import add_product, list_active, retire, set_price
from shelfstock.db import Store


def test_add_and_list_active():
    store = Store(":memory:")
    add_product(store, "A", "Alpha", 100)
    add_product(store, "B", "Beta", 200)
    retire(store, "B")
    names = [p.name for p in list_active(store)]
    assert names == ["Alpha"]


def test_price_cannot_go_negative():
    store = Store(":memory:")
    add_product(store, "A", "Alpha", 100)
    try:
        set_price(store, "A", -1)
    except ValueError:
        return
    raise AssertionError("expected ValueError")
