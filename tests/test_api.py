"""Tests for the standalone API client."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.toplofikacia_sofia.api import (
    API_URL,
    ToploAuthError,
    ToploClient,
    ToploConnectionError,
    parse_datetime,
    unwrap,
)

from .conftest import (
    INSTALLATION_ID,
    INSTALLATION_NUMBER,
    USER_ID,
    AUTH_FAIL,
    auth_ok,
    make_jwt,
    mock_api,
)


def test_unwrap_values_and_refs() -> None:
    payload = {
        "$id": "1",
        "a": {"$id": "2", "$values": [{"$id": "3", "x": 1}, {"$ref": "3"}]},
    }
    assert unwrap(payload) == {"a": [{"x": 1}, {"x": 1}]}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-31T00:00:00", datetime(2026, 10, 31, tzinfo=timezone.utc)),
        ("2026-10-31T00:00:00Z", datetime(2026, 10, 31, tzinfo=timezone.utc)),
        ("2026-10-31T10:00:00.1234567+02:00", datetime.fromisoformat("2026-10-31T10:00:00.123456+02:00")),
        (None, None),
        ("garbage", None),
    ],
)
def test_parse_datetime(raw, expected) -> None:
    assert parse_datetime(raw) == expected


async def test_full_fetch(hass: HomeAssistant, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    client = ToploClient(async_get_clientsession(hass), "a@b.c", "pw")
    await client.login()
    assert client.user_id == USER_ID

    installations = await client.get_installations()
    assert [i.number for i in installations] == [INSTALLATION_NUMBER, 999]
    assert installations[1].is_deactivated

    invoices = await client.get_invoices([INSTALLATION_NUMBER])
    assert [i.due for i in invoices] == [Decimal(0), Decimal("55.75")]
    assert invoices[0].is_paid and not invoices[1].is_paid

    rows = await client.get_consumption(INSTALLATION_ID, date(2026, 1, 1), date(2026, 12, 31))
    assert [(r.month, r.heating) for r in rows] == [(9, 0), (10, Decimal("0.4")), (11, 0)]

    accidents = await client.get_accidents()
    assert [a.is_active() for a in accidents] == [True, False]
    assert accidents[0].matches(["лозенец"])
    assert not accidents[0].matches(["Младост"])

    calls = {url.path: (url, headers) for _, url, _, headers in aioclient_mock.mock_calls}
    url, headers = calls["/api/installations/getbyuserid"]
    assert url.query["userId"] == USER_ID
    assert headers["Authorization"].startswith("Bearer ")
    assert headers["X-Client-Name"] == "MyPortal.Web"


async def test_invoice_query_params(hass: HomeAssistant, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    client = ToploClient(async_get_clientsession(hass), "a@b.c", "pw")
    await client.get_invoices([1, 2])
    url = next(u for _, u, _, _ in aioclient_mock.mock_calls if "invoices" in str(u))
    assert url.query["installationIds"] == "1,2"
    assert url.query["invoicePeriodFilter"] == "18"


async def test_login_rejected(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{API_URL}api/auth/login", json=AUTH_FAIL, status=400)
    client = ToploClient(async_get_clientsession(hass), "a@b.c", "bad")
    with pytest.raises(ToploAuthError, match="Invalid credentials"):
        await client.login()


async def test_expired_token_is_refreshed(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{API_URL}api/auth/login", json=auth_ok(make_jwt(exp_offset=-10)))
    aioclient_mock.post(f"{API_URL}api/auth/refreshToken", json=auth_ok(refresh="refresh-2"))
    aioclient_mock.get(f"{API_URL}api/accidents/get", json={"$id": "1", "$values": []})
    client = ToploClient(async_get_clientsession(hass), "a@b.c", "pw")
    await client.login()
    assert await client.get_accidents() == []

    paths = [str(u) for _, u, _, _ in aioclient_mock.mock_calls]
    assert paths.count(f"{API_URL}api/auth/login") == 1
    refresh_call = next(c for c in aioclient_mock.mock_calls if "refreshToken" in str(c[1]))
    assert refresh_call[2] == {"userId": USER_ID, "refreshToken": "refresh-1"}


async def test_failed_refresh_falls_back_to_login(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{API_URL}api/auth/login", json=auth_ok(make_jwt(exp_offset=-10)))
    aioclient_mock.post(f"{API_URL}api/auth/refreshToken", json=AUTH_FAIL, status=400)
    client = ToploClient(async_get_clientsession(hass), "a@b.c", "pw")
    await client.login()
    # Refresh fails, re-login returns another expired token -> still a usable header.
    aioclient_mock.get(f"{API_URL}api/accidents/get", json={"$id": "1", "$values": []})
    await client.get_accidents()
    paths = [str(u) for _, u, _, _ in aioclient_mock.mock_calls]
    assert paths.count(f"{API_URL}api/auth/login") == 2


async def test_sap_error(hass: HomeAssistant, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{API_URL}api/auth/login", json=auth_ok())
    aioclient_mock.get(
        f"{API_URL}api/invoices/getAll",
        json={"responseCode": 5, "responseCodeDescription": "SAP down", "item": None},
    )
    client = ToploClient(async_get_clientsession(hass), "a@b.c", "pw")
    with pytest.raises(ToploConnectionError, match="SAP down"):
        await client.get_invoices([1])


async def test_http_error(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(f"{API_URL}api/auth/login", json=auth_ok())
    aioclient_mock.get(f"{API_URL}api/accidents/get", status=500)
    client = ToploClient(async_get_clientsession(hass), "a@b.c", "pw")
    with pytest.raises(ToploConnectionError, match="HTTP 500"):
        await client.get_accidents()


async def test_stations(hass: HomeAssistant, aioclient_mock) -> None:
    mock_api(aioclient_mock)
    client = ToploClient(async_get_clientsession(hass), "a@b.c", "pw")
    [station] = await client.get_stations()
    assert station.name == "Ж.К. ТЕСТ Бл. 1 А"
    assert station.heating_temperature == 20.64
    assert station.supply_pressure is None
    assert station.unread_alarms == 53
    assert station.measured.isoformat() == "2026-11-25T09:21:46.124383+02:00"
