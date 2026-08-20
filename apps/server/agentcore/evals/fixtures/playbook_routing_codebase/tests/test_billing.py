from shelfstock.billing import apply_credit, tax_cents
from shelfstock.models import Invoice


def test_tax_truncates():
    assert tax_cents(100, 0.13) == 13
    assert tax_cents(10, 0.13) == 1


def test_credit_can_zero_out_invoice():
    invoice = Invoice("INV-1", "ORD-1", 1000, 130, 1130)
    apply_credit(invoice, 2000)
    assert invoice.paid is True
    assert invoice.total_cents == 0
