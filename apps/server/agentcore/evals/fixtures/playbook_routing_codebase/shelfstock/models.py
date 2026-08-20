"""Domain records. Quantity fields are integers; money is integer cents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class User:
    username: str
    password_hash: str
    role: str = "clerk"


@dataclass
class Product:
    sku: str
    name: str
    price_cents: int
    active: bool = True


@dataclass
class Warehouse:
    code: str
    name: str
    city: str


@dataclass
class StockLine:
    warehouse: str
    sku: str
    qty: int


@dataclass
class OrderLine:
    sku: str
    qty: int
    unit_price_cents: int


@dataclass
class Order:
    order_id: str
    warehouse: str
    customer: str
    lines: list[OrderLine] = field(default_factory=list)
    status: str = "open"
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat(timespec="seconds")


@dataclass
class Invoice:
    invoice_id: str
    order_id: str
    subtotal_cents: int
    tax_cents: int
    total_cents: int
    paid: bool = False
