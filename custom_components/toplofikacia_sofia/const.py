"""Constants for the Toplofikatsia Sofia integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "toplofikacia_sofia"
MANUFACTURER: Final = "Топлофикация София"
PORTAL_URL: Final = "https://my.toplo.bg"

CONF_ACCIDENT_KEYWORDS: Final = "accident_keywords"

ACCOUNT_SCAN_INTERVAL: Final = timedelta(hours=6)
ACCIDENTS_SCAN_INTERVAL: Final = timedelta(minutes=30)
STATIONS_SCAN_INTERVAL: Final = timedelta(minutes=10)
