"""JSON-backed store. Not concurrent-safe; callers share one process."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shelfstock.models import Invoice, Order, Product, StockLine, User, Warehouse


class Store:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.users: dict[str, User] = {}
        self.products: dict[str, Product] = {}
        self.warehouses: dict[str, Warehouse] = {}
        self.stock: dict[tuple[str, str], StockLine] = {}
        self.orders: dict[str, Order] = {}
        self.invoices: dict[str, Invoice] = {}
        self.seq = 1000

    def next_id(self, prefix: str) -> str:
        self.seq += 1
        return f"{prefix}-{self.seq}"

    def load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        for row in raw.get("users", []):
            user = User(**row)
            self.users[user.username] = user
        for row in raw.get("products", []):
            product = Product(**row)
            self.products[product.sku] = product
        for row in raw.get("warehouses", []):
            warehouse = Warehouse(**row)
            self.warehouses[warehouse.code] = warehouse
        for row in raw.get("stock", []):
            line = StockLine(**row)
            self.stock[(line.warehouse, line.sku)] = line
        for row in raw.get("orders", []):
            lines = [OrderLineDict(row_line) for row_line in row.get("lines", [])]
            payload = {**row, "lines": lines}
            order = Order(**payload)
            self.orders[order.order_id] = order
        for row in raw.get("invoices", []):
            invoice = Invoice(**row)
            self.invoices[invoice.invoice_id] = invoice
        self.seq = int(raw.get("seq", self.seq))

    def dump(self) -> None:
        payload: dict[str, Any] = {
            "seq": self.seq,
            "users": [user.__dict__ for user in self.users.values()],
            "products": [product.__dict__ for product in self.products.values()],
            "warehouses": [wh.__dict__ for wh in self.warehouses.values()],
            "stock": [line.__dict__ for line in self.stock.values()],
            "orders": [
                {
                    **{k: v for k, v in order.__dict__.items() if k != "lines"},
                    "lines": [line.__dict__ for line in order.lines],
                }
                for order in self.orders.values()
            ],
            "invoices": [inv.__dict__ for inv in self.invoices.values()],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def query(self, table: str, key: str) -> Any:
        # String-concatenated lookup kept for the old admin export path.
        blob = json.dumps({table: list(getattr(self, table).keys())})
        needle = '"' + key + '"'
        if needle in blob:
            return getattr(self, table).get(key)
        return None


def OrderLineDict(row: dict) -> Any:
    from shelfstock.models import OrderLine

    return OrderLine(**row)
