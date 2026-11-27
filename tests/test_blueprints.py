"""Run the bundled automation blueprints in a test Home Assistant."""

from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

BLUEPRINTS = Path(__file__).parent.parent / "blueprints" / "automation" / "toplofikacia_sofia"
HEATING_ON = "binary_sensor.as_test_heating_on"
THERMOSTATS = ["climate.living_room", "climate.bedroom"]


def _install(hass: HomeAssistant, name: str) -> str:
    dest = Path(hass.config.path("blueprints", "automation", "toplofikacia_sofia"))
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(BLUEPRINTS / name, dest / name)
    return f"toplofikacia_sofia/{name}"


def _phone(hass: HomeAssistant) -> str:
    entry = MockConfigEntry(domain="mobile_app")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("mobile_app", "phone")}, name="Test iPhone"
    )
    return device.id


async def test_pause_and_resume_thermostats(hass: HomeAssistant) -> None:
    hass.states.async_set(HEATING_ON, "on")
    for entity in THERMOSTATS:
        hass.states.async_set(entity, "heat", {"temperature": 21})
    created = async_mock_service(hass, "scene", "create")
    restored = async_mock_service(hass, "scene", "turn_on")
    modes = async_mock_service(hass, "climate", "set_hvac_mode")
    notified = async_mock_service(hass, "notify", "mobile_app_test_iphone")
    phone = _phone(hass)

    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "id": "pause",
                "use_blueprint": {
                    "path": _install(hass, "pause_thermostats.yaml"),
                    "input": {
                        "heating_on": HEATING_ON,
                        "thermostats": THERMOSTATS,
                        "notify_devices": [phone],
                    },
                },
            }
        },
    )
    await hass.async_block_till_done()
    assert [hass.states.get(e).state for e in hass.states.async_entity_ids("automation")] == ["on"]

    # A single off reading isn't enough.
    hass.states.async_set(HEATING_ON, "off")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=60))
    await hass.async_block_till_done()
    assert not modes

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=121))
    await hass.async_block_till_done()
    assert created[0].data["snapshot_entities"] == THERMOSTATS
    assert modes[0].data == {"entity_id": THERMOSTATS, "hvac_mode": "off"}
    assert "paused" in notified[0].data["message"]

    # The snapshot scene exists, so resuming restores it.
    automation = hass.states.async_entity_ids("automation")[0].split(".")[1]
    scene = f"scene.toplofikacia_{automation}_before_pause"
    assert created[0].data["scene_id"] == scene.split(".")[1]
    hass.states.async_set(scene, "2026-11-27T00:00:00+00:00")
    hass.states.async_set(HEATING_ON, "on")
    await hass.async_block_till_done()
    assert restored[0].data["entity_id"] == [scene]
    assert len(modes) == 1
    assert "back on" in notified[1].data["message"]


async def test_resume_without_snapshot_leaves_off(hass: HomeAssistant) -> None:
    hass.states.async_set(HEATING_ON, "off")
    modes = async_mock_service(hass, "climate", "set_hvac_mode")
    restored = async_mock_service(hass, "scene", "turn_on")
    notified = async_mock_service(hass, "notify", "mobile_app_test_iphone")
    phone = _phone(hass)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": _install(hass, "pause_thermostats.yaml"),
                    "input": {
                        "heating_on": HEATING_ON,
                        "thermostats": THERMOSTATS,
                        "notify_devices": [phone],
                    },
                },
            }
        },
    )
    await hass.async_block_till_done()
    hass.states.async_set(HEATING_ON, "on")
    await hass.async_block_till_done()
    assert not modes
    assert not restored
    assert "left off" in notified[0].data["message"]


async def test_temperature_alert(hass: HomeAssistant) -> None:
    sensors = {
        "heating_temperature": "sensor.as_test_heating_temperature",
        "heating_target": "sensor.as_test_heating_target_temperature",
        "hot_water_temperature": "sensor.as_test_hot_water_temperature",
        "hot_water_target": "sensor.as_test_hot_water_target_temperature",
    }
    hass.states.async_set(HEATING_ON, "on")
    hass.states.async_set(sensors["heating_temperature"], "50.0")
    hass.states.async_set(sensors["heating_target"], "56.0")
    hass.states.async_set(sensors["hot_water_temperature"], "55.0")
    hass.states.async_set(sensors["hot_water_target"], "55.0")
    notified = async_mock_service(hass, "notify", "mobile_app_test_iphone")
    phone = _phone(hass)

    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": _install(hass, "temperature_alert.yaml"),
                    "input": {**sensors, "heating_on": HEATING_ON, "notify_devices": [phone]},
                },
            }
        },
    )
    await hass.async_block_till_done()

    # 45 is about 20% under 56 but not quite (44.8 is the line).
    hass.states.async_set(sensors["heating_temperature"], "45.0")
    await hass.async_block_till_done()
    assert not notified

    hass.states.async_set(sensors["hot_water_temperature"], "43.0")
    await hass.async_block_till_done()
    assert len(notified) == 1
    assert notified[0].data["message"] == "Hot water is 43.0 °C against a 55.0 °C target."

    # Still low: no repeat until it recovers and drops again.
    hass.states.async_set(sensors["hot_water_temperature"], "42.0")
    await hass.async_block_till_done()
    assert len(notified) == 1

    # Heating is ignored while the season is off.
    hass.states.async_set(HEATING_ON, "off")
    hass.states.async_set(sensors["heating_temperature"], "21.0")
    await hass.async_block_till_done()
    assert len(notified) == 1

    hass.states.async_set(HEATING_ON, "on")
    await hass.async_block_till_done()
    assert len(notified) == 2
    assert notified[1].data["title"] == "Toplofikatsia: heating is low"
