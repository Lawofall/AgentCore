"""Minimal HTTP-ish handlers used by the CLI and tests. No framework."""

from __future__ import annotations

from typing import Any, Callable

from shelfstock import auth, billing, catalog, orders, reports, warehouse
from shelfstock.config import Settings
from shelfstock.db import Store

Handler = Callable[[Store, Settings, dict[str, Any]], dict[str, Any]]


def _ok(data: object) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _err(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def handle(store: Store, settings: Settings, request: dict[str, Any]) -> dict[str, Any]:
    route = str(request.get("route") or "")
    payload = request.get("payload") or {}
    token = str(request.get("token") or "")
    table = ROUTES.get(route)
    if table is None:
        return _err(f"unknown route {route}")
    need_auth, fn = table
    try:
        if need_auth:
            auth.require(token)
        return fn(store, settings, payload)
    except (KeyError, ValueError, PermissionError) as exc:
        return _err(str(exc))


def _login(store: Store, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    token = auth.login(
        store, settings, str(payload.get("username") or ""), str(payload.get("password") or "")
    )
    if token is None:
        return _err("bad credentials")
    return _ok({"token": token})


def _products(store: Store, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    _ = settings, payload
    return _ok(
        [
            {
                "sku": p.sku,
                "name": p.name,
                "price_cents": p.price_cents,
                "active": p.active,
            }
            for p in catalog.list_active(store)
        ]
    )


def _add_product(store: Store, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    _ = settings
    product = catalog.add_product(
        store,
        str(payload["sku"]),
        str(payload["name"]),
        int(payload["price_cents"]),
    )
    return _ok({"sku": product.sku})


def _receive(store: Store, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    _ = settings
    line = warehouse.receive(
        store, str(payload["warehouse"]), str(payload["sku"]), int(payload["qty"])
    )
    return _ok({"qty": line.qty})


def _place_order(store: Store, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    _ = settings
    order = orders.draft(store, str(payload["warehouse"]), str(payload["customer"]))
    for line in payload.get("lines") or []:
        orders.add_line(store, order.order_id, str(line["sku"]), int(line["qty"]))
    orders.confirm(store, order.order_id)
    invoice = billing.issue(store, settings, order.order_id)
    return _ok({"order_id": order.order_id, "invoice_id": invoice.invoice_id})


def _dashboard(store: Store, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    _ = payload
    return _ok(reports.dashboard(store, settings))


ROUTES: dict[str, tuple[bool, Handler]] = {
    "login": (False, _login),
    "products": (True, _products),
    "product.add": (True, _add_product),
    "stock.receive": (True, _receive),
    "order.place": (True, _place_order),
    "dashboard": (True, _dashboard),
}
