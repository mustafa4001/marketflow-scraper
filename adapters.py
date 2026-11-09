"""Registry of marketplace adapters so the engine can stay generic."""
from __future__ import annotations

from . import amazon, ebay, etsy

ADAPTERS = {
    amazon.NAME: amazon,
    ebay.NAME: ebay,
    etsy.NAME: etsy,
}


def get_adapter(marketplace: str):
    return ADAPTERS.get(marketplace)
