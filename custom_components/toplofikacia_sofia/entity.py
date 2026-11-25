"""Base entities for Toplofikatsia Sofia."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Consumption, Installation, Invoice, Station
from .const import DOMAIN, MANUFACTURER, PORTAL_URL
from .coordinator import (
    ToploAccidentsCoordinator,
    ToploAccountCoordinator,
    ToploStationsCoordinator,
)


class ToploInstallationEntity(CoordinatorEntity[ToploAccountCoordinator]):
    """An entity tied to one heating installation."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ToploAccountCoordinator, number: int, key: str
    ) -> None:
        super().__init__(coordinator)
        self._number = number
        self._attr_unique_id = f"{number}_{key}"
        self._attr_translation_key = key
        inst = coordinator.data.installations[number]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(number))},
            name=inst.name or f"Топлофикация {number}",
            manufacturer=MANUFACTURER,
            model="Абонат",
            serial_number=str(number),
            configuration_url=PORTAL_URL,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        return super().available and self._number in self.coordinator.data.installations

    @property
    def installation(self) -> Installation:
        return self.coordinator.data.installations[self._number]

    @property
    def invoices(self) -> list[Invoice]:
        return self.coordinator.data.invoices.get(self._number, [])

    @property
    def consumption(self) -> list[Consumption]:
        return self.coordinator.data.consumption.get(self._number, [])


class ToploAccountEntity(CoordinatorEntity[ToploAccidentsCoordinator]):
    """An account-wide entity (network accidents and repairs)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ToploAccidentsCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"account_{entry.unique_id}")},
            name=MANUFACTURER,
            manufacturer=MANUFACTURER,
            model="Аварии и ремонти",
            configuration_url=f"{PORTAL_URL}/accidents",
            entry_type=DeviceEntryType.SERVICE,
        )


class ToploStationEntity(CoordinatorEntity[ToploStationsCoordinator]):
    """An entity tied to one substation's live readings."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ToploStationsCoordinator, station_id: str, key: str
    ) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"station_{station_id}_{key}"
        self._attr_translation_key = key
        station = coordinator.data[station_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"station_{station_id}")},
            name=f"АС {station.name or station.station_number}",
            manufacturer=MANUFACTURER,
            model="Абонатна станция",
            serial_number=str(station.station_number),
            configuration_url=f"{PORTAL_URL}/monitoring/station/{station_id}",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        return super().available and self._station_id in self.coordinator.data

    @property
    def station(self) -> Station:
        return self.coordinator.data[self._station_id]
