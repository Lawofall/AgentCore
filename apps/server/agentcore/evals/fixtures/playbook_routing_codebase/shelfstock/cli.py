"""Command line for the inventory demo."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from shelfstock.auth import seed_admin
from shelfstock.catalog import add_product
from shelfstock.config import load_settings
from shelfstock.db import Store
from shelfstock.orders import add_line, confirm, draft
from shelfstock.reports import dashboard, stock_rows, to_csv
from shelfstock.warehouse import add_warehouse, receive


def _store(path: str) -> Store:
    store = Store(path)
    store.load()
    return store


def cmd_seed(args: argparse.Namespace) -> None:
    settings = load_settings(data_path=args.data)
    store = _store(settings.data_path)
    seed_admin(store, settings)
    add_warehouse(store, "WH-EAST", "East DC", "Shanghai")
    add_warehouse(store, "WH-WEST", "West DC", "Chengdu")
    add_product(store, "SKU-BOLT", "M8 bolt pack", 350)
    add_product(store, "SKU-NUT", "M8 nut pack", 180)
    add_product(store, "SKU-WASHER", "Washer mix", 90)
    receive(store, "WH-EAST", "SKU-BOLT", 40)
    receive(store, "WH-EAST", "SKU-NUT", 40)
    receive(store, "WH-WEST", "SKU-WASHER", 12)
    store.dump()
    print(f"seeded {settings.data_path}")


def cmd_order(args: argparse.Namespace) -> None:
    settings = load_settings(data_path=args.data)
    store = _store(settings.data_path)
    order = draft(store, args.warehouse, args.customer)
    for item in args.item:
        sku, qty_s = item.split(":", 1)
        add_line(store, order.order_id, sku, int(qty_s))
    confirm(store, order.order_id)
    store.dump()
    print(order.order_id)


def cmd_report(args: argparse.Namespace) -> None:
    settings = load_settings(data_path=args.data)
    store = _store(settings.data_path)
    if args.kind == "stock":
        Path(args.out).write_text(to_csv(stock_rows(store)), encoding="utf-8")
        return
    print(json.dumps(dashboard(store, settings), ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shelfstock")
    parser.add_argument("--data", default="shelfstock.json")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_seed = sub.add_parser("seed")
    p_seed.set_defaults(func=cmd_seed)
    p_order = sub.add_parser("order")
    p_order.add_argument("--warehouse", required=True)
    p_order.add_argument("--customer", required=True)
    p_order.add_argument("--item", action="append", required=True, help="SKU:qty")
    p_order.set_defaults(func=cmd_order)
    p_report = sub.add_parser("report")
    p_report.add_argument("--kind", choices=["dash", "stock"], default="dash")
    p_report.add_argument("--out", default="stock.csv")
    p_report.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
