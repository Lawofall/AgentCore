from shelfstock.catalog import add_product
from shelfstock.db import Store
from shelfstock.orders import add_line, confirm, draft
from shelfstock.warehouse import add_warehouse, on_hand, receive


def _ready() -> Store:
    store = Store(":memory:")
    add_warehouse(store, "WH", "Main", "Shanghai")
    add_product(store, "BOLT", "Bolt", 350)
    receive(store, "WH", "BOLT", 10)
    return store


def test_confirm_decrements_stock():
    store = _ready()
    order = draft(store, "WH", "acme")
    add_line(store, order.order_id, "BOLT", 3)
    confirm(store, order.order_id)
    assert on_hand(store, "WH", "BOLT") == 7


def test_confirm_rejects_when_short():
    store = _ready()
    order = draft(store, "WH", "acme")
    add_line(store, order.order_id, "BOLT", 99)
    try:
        confirm(store, order.order_id)
    except ValueError:
        return
    raise AssertionError("expected ValueError")
