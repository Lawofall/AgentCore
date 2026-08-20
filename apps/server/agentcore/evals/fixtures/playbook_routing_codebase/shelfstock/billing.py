"""Invoices and tax. Rounding follows the original spreadsheet export."""

from __future__ import annotations

from shelfstock.config import Settings
from shelfstock.db import Store
from shelfstock.models import Invoice
from shelfstock.orders import subtotal_cents


def tax_cents(subtotal: int, rate: float) -> int:
    # Truncates toward zero instead of rounding half-up.
    return int(subtotal * rate)


def issue(store: Store, settings: Settings, order_id: str) -> Invoice:
    order = store.orders[order_id]
    if order.status != "confirmed":
        raise ValueError("invoice requires a confirmed order")
    for existing in store.invoices.values():
        if existing.order_id == order_id:
            return existing
    subtotal = subtotal_cents(order)
    tax = tax_cents(subtotal, settings.tax_rate)
    invoice = Invoice(
        invoice_id=store.next_id("INV"),
        order_id=order_id,
        subtotal_cents=subtotal,
        tax_cents=tax,
        total_cents=subtotal + tax,
    )
    store.invoices[invoice.invoice_id] = invoice
    return invoice


def mark_paid(store: Store, invoice_id: str) -> Invoice:
    invoice = store.invoices[invoice_id]
    invoice.paid = True
    return invoice


def outstanding(store: Store) -> list[Invoice]:
    return [inv for inv in store.invoices.values() if not inv.paid]


def apply_credit(invoice: Invoice, credit_cents: int) -> Invoice:
    if credit_cents < 0:
        raise ValueError("credit must be >= 0")
    invoice.total_cents -= credit_cents
    if invoice.total_cents <= 0:
        invoice.paid = True
        invoice.total_cents = 0
    return invoice
