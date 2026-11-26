"""Diagnostics test."""

from __future__ import annotations


from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import json_dumps
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.toplofikacia_sofia.const import CONF_ACCIDENT_KEYWORDS, DOMAIN
from custom_components.toplofikacia_sofia.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import INSTALLATION_NUMBER, USER_ID, mock_api


async def test_diagnostics_redacts(hass: HomeAssistant, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USER_ID,
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw"},
        options={CONF_ACCIDENT_KEYWORDS: "Черни връх"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, entry)
    dumped = json_dumps(diag)  # HA's encoder, as the download endpoint uses
    for secret in (
        "me@example.com",
        "pw\"",
        str(INSTALLATION_NUMBER),
        "Иван",
        "Лозенец",
        "100002",
        "Апартамент",  # installation name
        "4567",  # installation's station number
        "Черни връх",  # accident keywords
    ):
        assert secret not in dumped, secret
    assert len(diag["invoices"]["installation_1"]) == 2
