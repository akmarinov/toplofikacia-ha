"""Setup, entity and statistics tests."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.toplofikacia_sofia.api import API_URL
from custom_components.toplofikacia_sofia.const import CONF_ACCIDENT_KEYWORDS, DOMAIN
from custom_components.toplofikacia_sofia.coordinator import next_station_poll

from .conftest import AUTH_FAIL, INSTALLATION_NUMBER, STATION_ID, USER_ID, mock_api


def _entry(options: dict | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=USER_ID,
        title="me@example.com",
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw"},
        options=options or {},
    )


def _state(hass: HomeAssistant, platform: str, key: str):
    registry = er.async_get(hass)
    unique_id = f"{INSTALLATION_NUMBER}_{key}" if key not in ("active_accidents", "outage") else f"{USER_ID}_{key}"
    entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id, f"missing {platform} {key}"
    return hass.states.get(entity_id)


@pytest.mark.freeze_time("2026-11-25T10:00:00+02:00")
async def test_setup_and_entities(
    hass: HomeAssistant, aioclient_mock
) -> None:
    await hass.config.async_set_time_zone("Europe/Sofia")
    mock_api(aioclient_mock)
    entry = _entry({CONF_ACCIDENT_KEYWORDS: "лозенец"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    balance = _state(hass, "sensor", "balance_due")
    assert float(balance.state) == 55.75
    assert balance.attributes["unit_of_measurement"] == "EUR"
    assert len(balance.attributes["unpaid_documents"]) == 1

    assert _state(hass, "sensor", "unpaid_invoices").state == "1"
    assert _state(hass, "sensor", "next_due_date").state == "2026-11-30"

    latest = _state(hass, "sensor", "latest_invoice")
    assert float(latest.state) == 55.75
    assert latest.attributes["document_number"] == "100002"

    heating = _state(hass, "sensor", "heating_energy")
    assert float(heating.state) == 0.4
    assert heating.attributes["period"] == "2026-10"
    assert heating.attributes["unit_of_measurement"] == "MWh"
    assert float(_state(hass, "sensor", "hot_water_energy").state) == 0.15

    assert _state(hass, "binary_sensor", "overdue").state == "off"

    building = _state(hass, "sensor", "building_energy")
    assert float(building.state) == 31.5
    assert building.attributes["period"] == "2026-10"

    # Latest season = the two 2025-05..2026-04 price periods: (2.5-2.0) + (1.25-1.5).
    correction = _state(hass, "sensor", "annual_correction_heating")
    assert float(correction.state) == 0.25
    assert correction.attributes["period_from"] == "2025-05-01"
    assert correction.attributes["period_to"] == "2026-04-30"
    assert correction.attributes["invoiced"] == 3.5
    assert len(correction.attributes["price_periods"]) == 2
    assert round(float(_state(hass, "sensor", "annual_correction_hot_water").state), 3) == -0.15

    accidents = _state(hass, "sensor", "active_accidents")
    assert accidents.state == "1"
    assert accidents.attributes["events"][0]["region"] == "Лозенец"
    assert _state(hass, "binary_sensor", "outage").state == "on"

    # Live substation readings.
    registry = er.async_get(hass)

    def station_state(key: str):
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"station_{STATION_ID}_{key}")
        return entity_id and hass.states.get(entity_id)

    assert float(station_state("outside_temperature").state) == -1.61
    assert float(station_state("heating_temperature").state) == 20.64
    assert float(station_state("heating_set_temperature").state) == 64.58
    assert float(station_state("hot_water_temperature").state) == 16.78
    assert float(station_state("station_energy").state) == 5434.2
    assert station_state("flow").attributes["unit_of_measurement"] == "m³/h"
    assert station_state("last_measurement").state == "2026-11-25T07:21:46+00:00"
    # Fields the station doesn't report get no entity.
    assert station_state("supply_pressure") is None
    assert station_state("internal_temperature") is None
    heating_on = hass.states.get(
        registry.async_get_entity_id("binary_sensor", DOMAIN, f"station_{STATION_ID}_heating_on")
    )
    assert heating_on.state == "off"  # loop at 20.64 C, season not started
    device = dr.async_get(hass).async_get_device({(DOMAIN, f"station_{STATION_ID}")})
    assert device.name == "АС Ж.К. ТЕСТ Бл. 1 А"

    # Deactivated installation 999 gets no entities.
    assert not registry.async_get_entity_id("sensor", DOMAIN, "999_balance_due")

    # Monthly consumption imported as external statistics.
    await async_wait_recording_done(hass)
    stat_id = f"{DOMAIN}:heating_{INSTALLATION_NUMBER}"
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 1, 1, tzinfo=dt_util.UTC),
        None,
        {stat_id},
        "hour",
        None,
        {"state", "sum"},
    )
    assert [round(r["sum"], 3) for r in stats[stat_id]] == [0.0, 0.4, 0.4]
    building_id = f"{DOMAIN}:building_energy_{INSTALLATION_NUMBER}"
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 1, 1, tzinfo=dt_util.UTC),
        None,
        {building_id},
        "hour",
        None,
        {"sum"},
    )
    assert [round(r["sum"], 3) for r in stats[building_id]] == [9.5, 41.0]


@pytest.mark.freeze_time("2026-10-15T10:00:00+02:00")
async def test_future_months_not_imported(hass: HomeAssistant, aioclient_mock) -> None:
    await hass.config.async_set_time_zone("Europe/Sofia")
    mock_api(aioclient_mock)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)
    stat_id = f"{DOMAIN}:heating_{INSTALLATION_NUMBER}"
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 1, 1, tzinfo=dt_util.UTC),
        None,
        {stat_id},
        "hour",
        None,
        {"sum"},
    )
    # The November row from the API is in the future and must be skipped.
    assert len(stats[stat_id]) == 2


async def test_clearing_keywords_removes_outage_entity(
    hass: HomeAssistant, aioclient_mock
) -> None:
    mock_api(aioclient_mock)
    entry = _entry({CONF_ACCIDENT_KEYWORDS: "лозенец"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, f"{USER_ID}_outage")

    hass.config_entries.async_update_entry(entry, options={CONF_ACCIDENT_KEYWORDS: ""})
    await hass.async_block_till_done()
    assert not registry.async_get_entity_id("binary_sensor", DOMAIN, f"{USER_ID}_outage")

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.freeze_time("2026-12-05T10:00:00+02:00")
async def test_overdue_and_no_outage_sensor_without_keywords(
    hass: HomeAssistant, aioclient_mock
) -> None:
    mock_api(aioclient_mock)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    overdue = _state(hass, "binary_sensor", "overdue")
    assert overdue.state == "on"
    assert overdue.attributes["overdue_amount"] == 55.75
    registry = er.async_get(hass)
    assert not registry.async_get_entity_id("binary_sensor", DOMAIN, f"{USER_ID}_outage")


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.post(f"{API_URL}api/auth/login", json=AUTH_FAIL, status=400)
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_api_down_retries(
    hass: HomeAssistant, aioclient_mock
) -> None:
    aioclient_mock.post(f"{API_URL}api/auth/login", status=503, text="")
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_station_failure_does_not_block_setup(hass: HomeAssistant, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    # Override the stations endpoint with a failure (first match wins, so rebuild).
    aioclient_mock._mocks = [m for m in aioclient_mock._mocks if "GetStationStandartView" not in str(m.url)]
    aioclient_mock.get(f"{API_URL}api/Stations/GetStationStandartView", status=500)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert float(_state(hass, "sensor", "balance_due").state) == 55.75


@pytest.mark.parametrize(
    ("minutes_since_reading", "expected_minutes"),
    [
        (0, 60),  # fresh reading: wait for the next one (clamped to an hour)
        (20, 45),  # next reading due at +65 min
        (58, 10),  # due in 7 min: never poll faster than the retry interval
        (90, 10),  # overdue: keep retrying
    ],
)
def test_next_station_poll(minutes_since_reading: int, expected_minutes: int) -> None:
    now = datetime(2026, 11, 25, 10, 0, tzinfo=dt_util.UTC)
    measured = [now - timedelta(minutes=minutes_since_reading), now - timedelta(hours=3)]
    assert next_station_poll(measured, now) == timedelta(minutes=expected_minutes)
    assert next_station_poll([], now) == timedelta(minutes=10)


async def test_secondary_endpoint_failure_keeps_billing(hass: HomeAssistant, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    aioclient_mock._mocks = [
        m for m in aioclient_mock._mocks if "annualAligning" not in str(m.url)
    ]
    aioclient_mock.get(f"{API_URL}api/annualAligning/get", status=500)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert float(_state(hass, "sensor", "balance_due").state) == 55.75
    assert _state(hass, "sensor", "annual_correction_heating").state == "unknown"
