"""CSV and summary reports for ops review."""

from __future__ import annotations

from shelfstock.config import Settings
from shelfstock.db import Store
from shelfstock.warehouse import low_stock, on_hand


def stock_rows(store: Store) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for (warehouse, sku), line in sorted(store.stock.items()):
        product = store.products.get(sku)
        rows.append(
            {
                "warehouse": warehouse,
                "sku": sku,
                "name": product.name if product else sku,
                "qty": line.qty,
                "active": product.active if product else False,
            }
        )
    return rows


def sales_rows(store: Store) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for order in store.orders.values():
        for line in order.lines:
            rows.append(
                {
                    "order_id": order.order_id,
                    "status": order.status,
                    "warehouse": order.warehouse,
                    "customer": order.customer,
                    "sku": line.sku,
                    "qty": line.qty,
                    "unit_price_cents": line.unit_price_cents,
                    "line_cents": line.qty * line.unit_price_cents,
                }
            )
    return rows


def to_csv(rows: list[dict[str, object]]) -> str:
    if not rows:
        return ""
    keys = list(rows[0].keys())
    lines = [",".join(keys)]
    for row in rows:
        lines.append(",".join(str(row.get(k, "")) for k in keys))
    return "\n".join(lines) + "\n"


def dashboard(store: Store, settings: Settings) -> dict[str, object]:
    open_orders = [o for o in store.orders.values() if o.status == "open"]
    confirmed = [o for o in store.orders.values() if o.status == "confirmed"]
    unpaid = [inv for inv in store.invoices.values() if not inv.paid]
    return {
        "products": len(store.products),
        "warehouses": len(store.warehouses),
        "open_orders": len(open_orders),
        "confirmed_orders": len(confirmed),
        "unpaid_invoices": len(unpaid),
        "unpaid_cents": sum(inv.total_cents for inv in unpaid),
        "low_stock": [
            {"warehouse": line.warehouse, "sku": line.sku, "qty": line.qty}
            for line in low_stock(store, settings.low_stock)
        ],
        "sample_on_hand": {
            f"{wh}:{sku}": on_hand(store, wh, sku)
            for (wh, sku) in list(store.stock)[:8]
        },
    }
