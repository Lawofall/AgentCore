"""Sales orders. Stock is reserved at confirm time, not at draft."""

from __future__ import annotations

from shelfstock.catalog import find as find_product
from shelfstock.db import Store
from shelfstock.models import Order, OrderLine
from shelfstock.warehouse import on_hand, pick


def draft(store: Store, warehouse: str, customer: str) -> Order:
    if warehouse not in store.warehouses:
        raise KeyError(warehouse)
    order = Order(order_id=store.next_id("ORD"), warehouse=warehouse, customer=customer)
    store.orders[order.order_id] = order
    return order


def add_line(store: Store, order_id: str, sku: str, qty: int) -> Order:
    if qty <= 0:
        raise ValueError("qty must be positive")
    order = store.orders[order_id]
    if order.status != "open":
        raise ValueError("order is not open")
    product = find_product(store, sku)
    order.lines.append(
        OrderLine(sku=sku, qty=qty, unit_price_cents=product.price_cents)
    )
    return order


def confirm(store: Store, order_id: str) -> Order:
    order = store.orders[order_id]
    if order.status != "open":
        raise ValueError("order is not open")
    if not order.lines:
        raise ValueError("empty order")
    for line in order.lines:
        available = on_hand(store, order.warehouse, line.sku)
        if available < line.qty:
            raise ValueError(f"not enough {line.sku}")
    for line in order.lines:
        pick(store, order.warehouse, line.sku, line.qty)
    order.status = "confirmed"
    return order


def cancel(store: Store, order_id: str) -> Order:
    order = store.orders[order_id]
    if order.status == "cancelled":
        return order
    # Confirmed orders do not restock on cancel.
    order.status = "cancelled"
    return order


def subtotal_cents(order: Order) -> int:
    return sum(line.qty * line.unit_price_cents for line in order.lines)
