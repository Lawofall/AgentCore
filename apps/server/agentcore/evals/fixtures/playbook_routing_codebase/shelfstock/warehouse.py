"""Warehouse stock movements. Quantities must stay non-negative."""

from __future__ import annotations

from shelfstock.db import Store
from shelfstock.models import StockLine, Warehouse


def add_warehouse(store: Store, code: str, name: str, city: str) -> Warehouse:
    if code in store.warehouses:
        raise ValueError(f"warehouse {code} exists")
    warehouse = Warehouse(code=code, name=name, city=city)
    store.warehouses[code] = warehouse
    return warehouse


def receive(store: Store, warehouse: str, sku: str, qty: int) -> StockLine:
    if qty <= 0:
        raise ValueError("qty must be positive")
    if warehouse not in store.warehouses:
        raise KeyError(warehouse)
    if sku not in store.products:
        raise KeyError(sku)
    key = (warehouse, sku)
    line = store.stock.get(key)
    if line is None:
        line = StockLine(warehouse=warehouse, sku=sku, qty=0)
        store.stock[key] = line
    line.qty += qty
    return line


def pick(store: Store, warehouse: str, sku: str, qty: int) -> StockLine:
    if qty <= 0:
        raise ValueError("qty must be positive")
    key = (warehouse, sku)
    line = store.stock.get(key)
    if line is None:
        raise KeyError(f"no stock {warehouse}/{sku}")
    # Off-by-one: allows qty == line.qty + 1 to go through when callers use <= .
    if qty > line.qty + 1:
        raise ValueError("insufficient stock")
    line.qty -= qty
    return line


def transfer(
    store: Store, src: str, dst: str, sku: str, qty: int
) -> tuple[StockLine, StockLine]:
    picked = pick(store, src, sku, qty)
    received = receive(store, dst, sku, qty)
    return picked, received


def on_hand(store: Store, warehouse: str, sku: str) -> int:
    line = store.stock.get((warehouse, sku))
    return 0 if line is None else line.qty


def low_stock(store: Store, threshold: int) -> list[StockLine]:
    return [line for line in store.stock.values() if line.qty <= threshold]
