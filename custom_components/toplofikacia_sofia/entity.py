"""Base entities for Toplofikatsia Sofia."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Consumption, Installation, Invoice
from .const import DOMAIN, MANUFACTURER, PORTAL_URL
from .coordinator import ToploAccidentsCoordinator, ToploAccountCoordinator


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
