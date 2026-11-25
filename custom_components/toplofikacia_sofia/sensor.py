"""Sensors for Toplofikatsia Sofia."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ANNUAL_CORRECTION_OK, Accident, AnnualCorrection, Consumption, Invoice, Station
from .const import CONF_ACCIDENT_KEYWORDS
from .coordinator import ToploConfigEntry
from .entity import ToploAccountEntity, ToploInstallationEntity, ToploStationEntity

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


def _building_energy(e: ToploInstallationEntity) -> float | None:
    row = latest_consumption(e.building_consumption)
    return float(row.total) if row else None


def latest_season(rows: list[AnnualCorrection]) -> list[AnnualCorrection]:
    """Price periods of the most recent generated annual correction (~12 months)."""
    done = [r for r in rows if r.status == ANNUAL_CORRECTION_OK and r.period_to]
    if not done:
        return []
    end = max(r.period_to for r in done)
    start = end - timedelta(days=366)
    return [r for r in done if r.period_from and r.period_from >= start]


def _correction(e: ToploInstallationEntity, kind: str) -> float | None:
    season = latest_season(e.annual_corrections)
    if not season:
        return None
    return float(sum((getattr(r, f"{kind}_difference") for r in season), Decimal(0)))


def _correction_attrs(e: ToploInstallationEntity, kind: str) -> dict[str, Any]:
    season = latest_season(e.annual_corrections)
    if not season:
        return {}
    invoiced = sum((getattr(r, f"{kind}_invoiced") for r in season), Decimal(0))
    final = sum((getattr(r, f"{kind}_final") for r in season), Decimal(0))
    return {
        "period_from": min(r.period_from for r in season).date().isoformat(),
        "period_to": max(r.period_to for r in season).date().isoformat(),
        "invoiced": float(invoiced),
        "final": float(final),
        "price_periods": [
            {
                "from": r.period_from.date().isoformat(),
                "to": r.period_to.date().isoformat(),
                "invoiced": float(getattr(r, f"{kind}_invoiced")),
                "final": float(getattr(r, f"{kind}_final")),
            }
            for r in season
        ],
    }


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
        # The station's heat meter reports one total (the API puts it all in
        # heatingEnergy, hot water stays 0), so expose it as one figure.
        key="building_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.MEGA_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda e: _building_energy(e),
        attrs_fn=lambda e: _consumption_attrs(latest_consumption(e.building_consumption)),
    ),
    InstallationSensorDescription(
        key="annual_correction_heating",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.MEGA_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda e: _correction(e, "heating"),
        attrs_fn=lambda e: _correction_attrs(e, "heating"),
    ),
    InstallationSensorDescription(
        key="annual_correction_hot_water",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.MEGA_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda e: _correction(e, "hot_water"),
        attrs_fn=lambda e: _correction_attrs(e, "hot_water"),
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


@dataclass(frozen=True, kw_only=True)
class StationSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Station], Any]
    # Only create the entity if the station reported a value at setup.
    optional: bool = False


def _temperature(key: str, fn: Callable[[Station], float | None], **kwargs: Any) -> StationSensorDescription:
    return StationSensorDescription(
        key=key,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=fn,
        **kwargs,
    )


STATION_SENSORS: tuple[StationSensorDescription, ...] = (
    _temperature("outside_temperature", lambda s: s.outside_temperature),
    _temperature("internal_temperature", lambda s: s.internal_temperature, optional=True),
    _temperature("heating_temperature", lambda s: s.heating_temperature),
    _temperature("heating_set_temperature", lambda s: s.heating_set_temperature),
    _temperature("hot_water_temperature", lambda s: s.hot_water_temperature),
    _temperature("hot_water_set_temperature", lambda s: s.hot_water_set_temperature),
    _temperature("supply_temperature", lambda s: s.supply_temperature),
    _temperature("return_temperature", lambda s: s.return_temperature),
    StationSensorDescription(
        key="flow",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        # The API reports litres per hour (the portal labels it "l/h").
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_HOUR,
        suggested_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        suggested_display_precision=2,
        value_fn=lambda s: s.flow,
    ),
    StationSensorDescription(
        key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=1,
        value_fn=lambda s: s.power,
    ),
    StationSensorDescription(
        key="station_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.MEGA_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda s: s.energy,
    ),
    StationSensorDescription(
        key="supply_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        suggested_display_precision=2,
        value_fn=lambda s: s.supply_pressure,
        optional=True,
    ),
    StationSensorDescription(
        key="return_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        suggested_display_precision=2,
        value_fn=lambda s: s.return_pressure,
        optional=True,
    ),
    StationSensorDescription(
        key="unread_alarms",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.unread_alarms,
    ),
    StationSensorDescription(
        key="last_measurement",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.measured,
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
    for station_id, station in (runtime.stations.data or {}).items():
        entities.extend(
            ToploStationSensor(runtime.stations, station_id, desc)
            for desc in STATION_SENSORS
            if not desc.optional or desc.value_fn(station) is not None
        )
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


class ToploStationSensor(ToploStationEntity, SensorEntity):
    """A live substation reading."""

    entity_description: StationSensorDescription

    def __init__(self, coordinator, station_id: str, description: StationSensorDescription) -> None:
        super().__init__(coordinator, station_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.station)


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
