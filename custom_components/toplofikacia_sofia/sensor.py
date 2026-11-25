"""Sensors for Toplofikatsia Sofia."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Accident, Consumption, Invoice
from .const import CONF_ACCIDENT_KEYWORDS
from .coordinator import ToploConfigEntry
from .entity import ToploAccountEntity, ToploInstallationEntity

PARALLEL_UPDATES = 0
# Bulgaria has billed in euro since 2026-01-01.
DEFAULT_CURRENCY = "EUR"


def unpaid(invoices: list[Invoice]) -> list[Invoice]:
    return [inv for inv in invoices if inv.due > 0]


def currency_of(invoices: list[Invoice], fallback: str) -> str:
    for inv in reversed(invoices):
        if inv.currency:
            return inv.currency
    return fallback


def latest_consumption(rows: list[Consumption]) -> Consumption | None:
    """Latest month that has any consumption recorded."""
    for row in reversed(rows):
        if row.total > 0 or row.is_real:
            return row
    return rows[-1] if rows else None


def keywords_from(raw: str | None) -> list[str]:
    return [k.strip() for k in (raw or "").split(",") if k.strip()]


def _invoice_attrs(inv: Invoice | None) -> dict[str, Any]:
    if inv is None:
        return {}
    return {
        "document_number": inv.document_number,
        "document_type": inv.document_type,
        "document_date": inv.document_date.date().isoformat() if inv.document_date else None,
        "due_date": inv.due_date.date().isoformat() if inv.due_date else None,
        "amount": float(inv.amount),
        "interest": float(inv.interest),
        "paid": float(inv.paid),
        "due": float(inv.due),
        "currency": inv.currency,
    }


def _consumption_attrs(row: Consumption | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {"period": row.period.strftime("%Y-%m"), "is_real_reading": row.is_real}


@dataclass(frozen=True, kw_only=True)
class InstallationSensorDescription(SensorEntityDescription):
    value_fn: Callable[[ToploInstallationEntity], Any]
    attrs_fn: Callable[[ToploInstallationEntity], dict[str, Any]] = lambda _: {}
    monetary: bool = False


def _sum_due(e: ToploInstallationEntity) -> Decimal:
    return sum((inv.due for inv in e.invoices), Decimal(0))


def _next_due(e: ToploInstallationEntity) -> date | None:
    dates = [inv.due_date.date() for inv in unpaid(e.invoices) if inv.due_date]
    return min(dates) if dates else None


def _latest_invoice(e: ToploInstallationEntity) -> Invoice | None:
    return e.invoices[-1] if e.invoices else None


def _energy(e: ToploInstallationEntity, field: str) -> float | None:
    row = latest_consumption(e.consumption)
    return float(getattr(row, field)) if row else None


INSTALLATION_SENSORS: tuple[InstallationSensorDescription, ...] = (
    InstallationSensorDescription(
        key="balance_due",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        monetary=True,
        value_fn=_sum_due,
        attrs_fn=lambda e: {
            "unpaid_documents": [_invoice_attrs(inv) for inv in unpaid(e.invoices)]
        },
    ),
    InstallationSensorDescription(
        key="unpaid_invoices",
        value_fn=lambda e: len(unpaid(e.invoices)),
    ),
    InstallationSensorDescription(
        key="next_due_date",
        device_class=SensorDeviceClass.DATE,
        value_fn=_next_due,
    ),
    InstallationSensorDescription(
        key="latest_invoice",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        monetary=True,
        value_fn=lambda e: (inv.total if (inv := _latest_invoice(e)) else None),
        attrs_fn=lambda e: _invoice_attrs(_latest_invoice(e)),
    ),
    InstallationSensorDescription(
        key="heating_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.MEGA_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda e: _energy(e, "heating"),
        attrs_fn=lambda e: _consumption_attrs(latest_consumption(e.consumption)),
    ),
    InstallationSensorDescription(
        key="hot_water_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.MEGA_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda e: _energy(e, "hot_water"),
        attrs_fn=lambda e: _consumption_attrs(latest_consumption(e.consumption)),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ToploConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entities: list[SensorEntity] = [
        ToploInstallationSensor(runtime.account, number, desc)
        for number in runtime.account.data.installations
        for desc in INSTALLATION_SENSORS
    ]
    entities.append(
        ToploAccidentsSensor(
            runtime.accidents, keywords_from(entry.options.get(CONF_ACCIDENT_KEYWORDS))
        )
    )
    async_add_entities(entities)


class ToploInstallationSensor(ToploInstallationEntity, SensorEntity):
    """A per-installation sensor."""

    entity_description: InstallationSensorDescription

    def __init__(self, coordinator, number: int, description: InstallationSensorDescription) -> None:
        super().__init__(coordinator, number, description.key)
        self.entity_description = description
        self._currency = DEFAULT_CURRENCY

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.monetary:
            # Keep the last currency seen so the unit doesn't flip when invoices are empty.
            self._currency = currency_of(self.invoices, self._currency)
            return self._currency
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attrs_fn(self)


def accident_attrs(acc: Accident) -> dict[str, Any]:
    return {
        "name": acc.name,
        "kind": "repair" if acc.is_repair else "accident",
        "region": acc.region,
        "addresses": acc.addresses,
        "affected_service": acc.affected_service,
        "from": acc.from_date.isoformat() if acc.from_date else None,
        "until": acc.until_date.isoformat() if acc.until_date else None,
    }


class ToploAccidentsSensor(ToploAccountEntity, SensorEntity):
    """Number of active accidents/repairs, optionally filtered by keywords."""

    def __init__(self, coordinator, keywords: list[str]) -> None:
        super().__init__(coordinator, "active_accidents")
        self._keywords = keywords

    def _matching(self) -> list[Accident]:
        return [
            a for a in self.coordinator.data or [] if a.is_active() and a.matches(self._keywords)
        ]

    @property
    def native_value(self) -> int:
        return len(self._matching())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "keywords": self._keywords,
            "events": [accident_attrs(a) for a in self._matching()],
        }
