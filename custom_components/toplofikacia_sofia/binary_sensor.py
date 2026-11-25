"""Binary sensors for Toplofikatsia Sofia."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_ACCIDENT_KEYWORDS, DOMAIN
from .coordinator import ToploConfigEntry
from .entity import ToploAccountEntity, ToploInstallationEntity, ToploStationEntity
from .sensor import accident_attrs, keywords_from, unpaid

PARALLEL_UPDATES = 0

# A heating loop above this is being fed by the network; when the season is
# off it drifts to room temperature (~20 C) while the set point stays high.
HEATING_ON_THRESHOLD = 30.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ToploConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        ToploOverdueSensor(runtime.account, number)
        for number in runtime.account.data.installations
    ]
    entities.extend(
        ToploHeatingOnSensor(runtime.stations, station_id)
        for station_id in (runtime.stations.data or {})
    )
    keywords = keywords_from(entry.options.get(CONF_ACCIDENT_KEYWORDS))
    if keywords:
        entities.append(ToploOutageSensor(runtime.accidents, keywords))
    else:
        # Keywords were cleared: drop the outage entity instead of leaving it unavailable.
        registry = er.async_get(hass)
        if entity_id := registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"{entry.unique_id}_outage"
        ):
            registry.async_remove(entity_id)
    async_add_entities(entities)


class ToploOverdueSensor(ToploInstallationEntity, BinarySensorEntity):
    """On when an unpaid document is past its due date."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, number: int) -> None:
        super().__init__(coordinator, number, "overdue")

    def _overdue(self):
        now = dt_util.utcnow()
        return [
            inv for inv in unpaid(self.invoices) if inv.due_date and inv.due_date < now
        ]

    @property
    def is_on(self) -> bool:
        return bool(self._overdue())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        overdue = self._overdue()
        return {
            "overdue_amount": float(sum(inv.due for inv in overdue)),
            "documents": [inv.document_number for inv in overdue],
        }


class ToploOutageSensor(ToploAccountEntity, BinarySensorEntity):
    """On when an active accident/repair matches the configured keywords."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, keywords: list[str]) -> None:
        super().__init__(coordinator, "outage")
        self._keywords = keywords

    def _matching(self):
        return [
            a for a in self.coordinator.data or [] if a.is_active() and a.matches(self._keywords)
        ]

    @property
    def is_on(self) -> bool:
        return bool(self._matching())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"events": [accident_attrs(a) for a in self._matching()]}


class ToploHeatingOnSensor(ToploStationEntity, BinarySensorEntity):
    """On when the substation's heating circuit is warm, i.e. the season is on."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, coordinator, station_id: str) -> None:
        super().__init__(coordinator, station_id, "heating_on")

    @property
    def is_on(self) -> bool | None:
        temperature = self.station.heating_temperature
        if temperature is None:
            return None
        return temperature >= HEATING_ON_THRESHOLD

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        station = self.station
        return {
            "heating_temperature": station.heating_temperature,
            "heating_set_temperature": station.heating_set_temperature,
            "threshold": HEATING_ON_THRESHOLD,
        }
