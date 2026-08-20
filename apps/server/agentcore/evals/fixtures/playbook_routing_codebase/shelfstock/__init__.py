"""ShelfStock inventory service."""

from shelfstock.config import Settings
from shelfstock.db import Store

__all__ = ["Settings", "Store", "VERSION"]

VERSION = "0.4.2"
