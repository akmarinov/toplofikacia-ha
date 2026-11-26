"""Diagnostics for Toplofikatsia Sofia."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_ACCIDENT_KEYWORDS
from .coordinator import ToploConfigEntry

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    "id",
    "owner",
    "address",
    "contract_number",
    "latitude",
    "longitude",
    "document_number",
    "number",
    "installation",
    "installation_number",
    "name",
    "station_number",
    "logical_heatmeter_number",
    # Users enter their district, street or station here.
    CONF_ACCIDENT_KEYWORDS,
}


def _plain(obj: object) -> dict[str, Any]:
    """asdict() with Decimals as floats so HA's JSON encoder accepts it."""
    return {
        k: float(v) if isinstance(v, Decimal) else v for k, v in asdict(obj).items()
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ToploConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    data = entry.runtime_data.account.data
    # Installation numbers identify the customer, so key by position instead.
    alias = {number: f"installation_{i}" for i, number in enumerate(data.installations, 1)}
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "installations": [
            async_redact_data(_plain(inst), TO_REDACT)
            for inst in data.installations.values()
        ],
        "invoices": {
            alias.get(number, "other"): [
                async_redact_data(_plain(inv), TO_REDACT) for inv in rows
            ]
            for number, rows in data.invoices.items()
        },
        "consumption": {
            alias.get(number, "other"): [
                async_redact_data(_plain(row), TO_REDACT) for row in rows
            ]
            for number, rows in data.consumption.items()
        },
        "accidents": len(entry.runtime_data.accidents.data or []),
        "stations": [
            async_redact_data(_plain(station), TO_REDACT)
            for station in (entry.runtime_data.stations.data or {}).values()
        ],
    }
