"""The Toplofikatsia Sofia integration."""

from __future__ import annotations

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ToploAuthError, ToploClient, ToploError
from .coordinator import (
    ToploAccidentsCoordinator,
    ToploAccountCoordinator,
    ToploConfigEntry,
    ToploRuntimeData,
    ToploStationsCoordinator,
)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ToploConfigEntry) -> bool:
    """Set up Toplofikatsia Sofia from a config entry."""
    client = ToploClient(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
    )
    try:
        await client.login()
    except ToploAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except ToploError as err:
        raise ConfigEntryNotReady(str(err)) from err

    account = ToploAccountCoordinator(hass, entry, client)
    accidents = ToploAccidentsCoordinator(hass, entry, client)
    stations = ToploStationsCoordinator(hass, entry, client)
    await account.async_config_entry_first_refresh()
    await accidents.async_config_entry_first_refresh()
    # Live readings are optional: a failure here must not block billing data.
    await stations.async_refresh()

    entry.runtime_data = ToploRuntimeData(client, account, accidents, stations)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ToploConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ToploConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
