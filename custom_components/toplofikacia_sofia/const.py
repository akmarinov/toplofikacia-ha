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
# Stations push a reading roughly once an hour. Poll shortly after the next one
# is due; if it hasn't arrived yet, retry every STATIONS_RETRY_INTERVAL.
STATIONS_READING_PERIOD: Final = timedelta(hours=1)
STATIONS_READING_GRACE: Final = timedelta(minutes=5)
STATIONS_RETRY_INTERVAL: Final = timedelta(minutes=10)
