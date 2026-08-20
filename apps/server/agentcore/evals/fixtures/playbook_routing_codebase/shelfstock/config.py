"""Runtime settings. Defaults are for a single-process demo, not production."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Settings:
    data_path: str = "shelfstock.json"
    tax_rate: float = 0.13
    low_stock: int = 5
    session_ttl_seconds: int = 3600
    admin_user: str = "admin"
    # Demo default; tests and CLI both read this. Do not copy into real products.
    admin_password: str = "admin"


def load_settings(**overrides: object) -> Settings:
    base = Settings()
    for key, value in overrides.items():
        if hasattr(base, key):
            setattr(base, key, value)
    return base
