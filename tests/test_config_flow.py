"""Config flow tests."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.toplofikacia_sofia.api import API_URL
from custom_components.toplofikacia_sofia.const import CONF_ACCIDENT_KEYWORDS, DOMAIN

from .conftest import AUTH_FAIL, USER_ID, auth_ok, make_jwt, mock_api


async def test_user_flow(hass: HomeAssistant, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: " me@example.com ", CONF_PASSWORD: "pw"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "me@example.com"
    assert result["data"] == {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw"}
    assert result["result"].unique_id == USER_ID


async def test_user_flow_invalid_auth_then_ok(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{API_URL}api/auth/login", json=AUTH_FAIL, status=400)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    mock_api(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_cannot_connect(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{API_URL}api/auth/login", status=503, text="")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_duplicate_account(hass: HomeAssistant, aioclient_mock) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=USER_ID, data={}).add_to_hass(hass)
    aioclient_mock.post(f"{API_URL}api/auth/login", json=auth_ok())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USER_ID,
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "old"},
    )
    entry.add_to_hass(hass)
    mock_api(aioclient_mock)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"


async def test_reauth_wrong_account(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USER_ID,
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "old"},
    )
    entry.add_to_hass(hass)
    aioclient_mock.post(
        f"{API_URL}api/auth/login", json=auth_ok(make_jwt(user_id="other-user"))
    )
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


async def test_options_flow(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USER_ID,
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ACCIDENT_KEYWORDS: "Лозенец, ул. Тест"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ACCIDENT_KEYWORDS] == "Лозенец, ул. Тест"
