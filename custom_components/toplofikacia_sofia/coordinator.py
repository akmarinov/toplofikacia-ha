"""Data update coordinators for Toplofikatsia Sofia."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, TypeVar

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .api import (
    Accident,
    AnnualCorrection,
    Consumption,
    Installation,
    Invoice,
    Station,
    ToploAuthError,
    ToploClient,
    ToploError,
)
from .const import (
    ACCIDENTS_SCAN_INTERVAL,
    ACCOUNT_SCAN_INTERVAL,
    DOMAIN,
    STATIONS_READING_GRACE,
    STATIONS_READING_PERIOD,
    STATIONS_RETRY_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")


@dataclass
class AccountData:
    """Everything fetched for the account in one refresh."""

    installations: dict[int, Installation] = field(default_factory=dict)
    invoices: dict[int, list[Invoice]] = field(default_factory=dict)
    consumption: dict[int, list[Consumption]] = field(default_factory=dict)
    # Whole-building (condominium) monthly consumption, per installation.
    building_consumption: dict[int, list[Consumption]] = field(default_factory=dict)
    annual_corrections: dict[int, list[AnnualCorrection]] = field(default_factory=dict)


@dataclass
class ToploRuntimeData:
    """Objects stored on the config entry."""

    client: ToploClient
    account: ToploAccountCoordinator
    accidents: ToploAccidentsCoordinator
    stations: ToploStationsCoordinator


type ToploConfigEntry = ConfigEntry[ToploRuntimeData]


def statistic_id(kind: str, installation_number: int) -> str:
    """External statistic id for a consumption series."""
    return f"{DOMAIN}:{kind}_{installation_number}"


class ToploAccountCoordinator(DataUpdateCoordinator[AccountData]):
    """Fetches installations, invoices and monthly consumption."""

    config_entry: ToploConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ToploConfigEntry, client: ToploClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} account",
            update_interval=ACCOUNT_SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> AccountData:
        try:
            installations = [
                inst
                for inst in await self.client.get_installations()
                if not inst.is_deactivated
            ]
            today = dt_util.now().date()
            start = date(today.year - 1, 1, 1)
            end = date(today.year, 12, 31)
            invoices, *consumption = await asyncio.gather(
                self.client.get_invoices([i.number for i in installations]),
                *(
                    self.client.get_consumption(inst.id, start, end)
                    for inst in installations
                ),
            )
        except ToploAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ToploError as err:
            raise UpdateFailed(str(err)) from err

        data = AccountData()
        for inst, rows in zip(installations, consumption, strict=True):
            data.installations[inst.number] = inst
            data.consumption[inst.number] = rows
            data.invoices[inst.number] = []
            data.building_consumption[inst.number] = await self._optional(
                self.client.get_station_consumption(inst.id, start, end),
                "building consumption",
            )
            data.annual_corrections[inst.number] = await self._optional(
                self.client.get_annual_corrections(inst.id, range(today.year - 3, today.year + 1)),
                "annual correction",
            )
        for invoice in invoices:
            data.invoices.setdefault(invoice.installation, []).append(invoice)
        for rows in data.invoices.values():
            rows.sort(key=lambda i: i.document_date or datetime.min.replace(tzinfo=dt_util.UTC))

        try:
            await self._async_import_statistics(data)
        except Exception:  # noqa: BLE001 - statistics must never break the update
            _LOGGER.exception("Failed to import consumption statistics")
        return data

    async def _optional(self, call: Coroutine[Any, Any, list[_T]], what: str) -> list[_T]:
        """Run a secondary fetch whose failure shouldn't fail the whole update."""
        try:
            return await call
        except ToploAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ToploError as err:
            _LOGGER.warning("Could not fetch %s: %s", what, err)
            return []

    async def _async_import_statistics(self, data: AccountData) -> None:
        """Write monthly consumption as external long-term statistics.

        Every refresh rewrites the fetched window, continuing the running sum
        from the last row stored before it, so revised months (estimates
        replaced by readings, annual corrections) are corrected in place.
        """
        tz = dt_util.get_default_time_zone()
        recorder = get_instance(self.hass)
        this_month = dt_util.now().date().replace(day=1)
        apartment = (
            ("heating", "heating", lambda c: c.heating),
            ("hot_water", "hot water", lambda c: c.hot_water),
        )
        # The building's heat meter reports a single total.
        building = (("building_energy", "building energy", lambda c: c.total),)
        series = [
            (number, rows, apartment) for number, rows in data.consumption.items()
        ] + [
            (number, rows, building)
            for number, rows in data.building_consumption.items()
        ]
        for number, all_rows, kinds in series:
            rows = [row for row in all_rows if row.period <= this_month]
            if not rows:
                continue
            inst = data.installations[number]
            label = inst.name or inst.address or str(number)
            for kind, title, value_of in kinds:
                stat_id = statistic_id(kind, number)
                first_start = datetime(rows[0].year, rows[0].month, 1, tzinfo=tz)
                previous = await recorder.async_add_executor_job(
                    statistics_during_period,
                    self.hass,
                    first_start - timedelta(days=400),
                    first_start,
                    {stat_id},
                    "hour",
                    None,
                    {"sum"},
                )
                base = 0.0
                if previous.get(stat_id):
                    base = float(previous[stat_id][-1].get("sum") or 0.0)

                running = base
                stats: list[StatisticData] = []
                for row in rows:
                    value = float(value_of(row))
                    running += value
                    stats.append(
                        StatisticData(
                            start=datetime(row.year, row.month, 1, tzinfo=tz),
                            state=value,
                            sum=running,
                        )
                    )
                metadata = StatisticMetaData(
                    mean_type=StatisticMeanType.NONE,
                    has_sum=True,
                    name=f"Toplofikatsia {label} {title}",
                    source=DOMAIN,
                    statistic_id=stat_id,
                    unit_class=EnergyConverter.UNIT_CLASS,
                    unit_of_measurement=UnitOfEnergy.MEGA_WATT_HOUR,
                )
                async_add_external_statistics(self.hass, metadata, stats)


class ToploAccidentsCoordinator(DataUpdateCoordinator[list[Accident]]):
    """Fetches network accidents and planned repairs."""

    config_entry: ToploConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ToploConfigEntry, client: ToploClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} accidents",
            update_interval=ACCIDENTS_SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> list[Accident]:
        culture = "bg-bg" if self.hass.config.language.startswith("bg") else "en-us"
        try:
            return await self.client.get_accidents(culture)
        except ToploAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ToploError as err:
            raise UpdateFailed(str(err)) from err


def next_station_poll(measured: list[datetime], now: datetime) -> timedelta:
    """Delay until the next station poll.

    Aim just after the next hourly reading of the most recently measured
    station; fall back to a short retry when no reading is due yet or the
    expected one is late.
    """
    if not measured:
        return STATIONS_RETRY_INTERVAL
    due = max(measured) + STATIONS_READING_PERIOD + STATIONS_READING_GRACE
    delay = due - now
    if delay < STATIONS_RETRY_INTERVAL:
        return STATIONS_RETRY_INTERVAL
    return min(delay, STATIONS_READING_PERIOD)


class ToploStationsCoordinator(DataUpdateCoordinator[dict[str, Station]]):
    """Fetches live substation readings (temperatures, flow, power)."""

    config_entry: ToploConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ToploConfigEntry, client: ToploClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} stations",
            update_interval=STATIONS_RETRY_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, Station]:
        # Retry soon if this poll fails; a success replaces it below.
        self.update_interval = STATIONS_RETRY_INTERVAL
        try:
            stations = await self.client.get_stations()
        except ToploAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ToploError as err:
            raise UpdateFailed(str(err)) from err
        self.update_interval = next_station_poll(
            [s.measured for s in stations if s.measured], dt_util.utcnow()
        )
        return {station.id: station for station in stations}
