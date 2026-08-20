"""Product catalog: create, update, list, retire."""

from __future__ import annotations

from shelfstock.db import Store
from shelfstock.models import Product


def add_product(store: Store, sku: str, name: str, price_cents: int) -> Product:
    if not sku or not name:
        raise ValueError("sku and name required")
    if price_cents < 0:
        raise ValueError("price must be >= 0")
    if sku in store.products and store.products[sku].active:
        raise ValueError(f"sku {sku} already exists")
    product = Product(sku=sku, name=name, price_cents=price_cents, active=True)
    store.products[sku] = product
    return product


def rename(store: Store, sku: str, name: str) -> Product:
    product = store.products[sku]
    product.name = name
    return product


def set_price(store: Store, sku: str, price_cents: int) -> Product:
    if price_cents < 0:
        raise ValueError("price must be >= 0")
    product = store.products[sku]
    product.price_cents = price_cents
    return product


def retire(store: Store, sku: str) -> None:
    store.products[sku].active = False


def list_active(store: Store) -> list[Product]:
    return [p for p in store.products.values() if p.active]


def find(store: Store, sku: str) -> Product:
    product = store.products.get(sku)
    if product is None:
        raise KeyError(sku)
    return product
