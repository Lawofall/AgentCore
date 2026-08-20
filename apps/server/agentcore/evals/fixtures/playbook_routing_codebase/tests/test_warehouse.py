from shelfstock.catalog import add_product
from shelfstock.db import Store
from shelfstock.warehouse import add_warehouse, on_hand, pick, receive, transfer


def test_receive_then_pick():
    store = Store(":memory:")
    add_warehouse(store, "E", "East", "SH")
    add_product(store, "NUT", "Nut", 80)
    receive(store, "E", "NUT", 5)
    pick(store, "E", "NUT", 2)
    assert on_hand(store, "E", "NUT") == 3


def test_transfer_moves_qty():
    store = Store(":memory:")
    add_warehouse(store, "E", "East", "SH")
    add_warehouse(store, "W", "West", "CD")
    add_product(store, "NUT", "Nut", 80)
    receive(store, "E", "NUT", 8)
    transfer(store, "E", "W", "NUT", 3)
    assert on_hand(store, "E", "NUT") == 5
    assert on_hand(store, "W", "NUT") == 3
