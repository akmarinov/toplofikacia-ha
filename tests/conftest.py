"""Fixtures for Toplofikatsia Sofia tests."""

from __future__ import annotations

import base64
import json
import time

import pytest

from custom_components.toplofikacia_sofia.api import API_URL

USER_ID = "3f1c9a52-0d6e-4c1b-9a77-2c7f0e9b1a10"
INSTALLATION_ID = "a0b1c2d3-e4f5-4607-8899-aabbccddeeff"
INSTALLATION_NUMBER = 123456


def make_jwt(exp_offset: int = 3600, user_id: str = USER_ID) -> str:
    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    claims = {
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier": user_id,
        "exp": int(time.time()) + exp_offset,
    }
    return f"{b64({'alg': 'HS256'})}.{b64(claims)}.sig"


def auth_ok(token: str | None = None, refresh: str = "refresh-1") -> dict:
    return {
        "$id": "1",
        "token": token or make_jwt(),
        "refreshToken": refresh,
        "isSuccess": True,
        "errors": {"$id": "2", "$values": []},
        "errorCode": None,
    }


AUTH_FAIL = {
    "$id": "1",
    "token": None,
    "refreshToken": None,
    "isSuccess": False,
    "errors": {"$id": "2", "$values": ["Invalid credentials."]},
    "errorCode": None,
}

INSTALLATIONS = {
    "$id": "1",
    "$values": [
        {
            "$id": "2",
            "id": INSTALLATION_ID,
            "number": INSTALLATION_NUMBER,
            "name": "Апартамент",
            "owner": "Иван Иванов",
            "address": "гр. София, ж.к. Лозенец, бл. 1",
            "longitude": 23.32,
            "latitude": 42.67,
            "businessPartner": 99,
            "contractNumber": "C-1",
            "giveUpPaperInvoice": True,
            "isConfirmed": True,
            "stationNumber": 4567,
            "logicalHeatmeterNumber": 1,
            "stationInstallationNumber": 1,
            "identificationNumber": None,
            "isDeactivated": False,
            "userId": USER_ID,
        },
        {
            "$id": "3",
            "id": "deadbeef-0000-0000-0000-000000000000",
            "number": 999,
            "name": "Old",
            "isDeactivated": True,
        },
    ],
}

INVOICES = {
    "$id": "1",
    "responseCode": 0,
    "responseCodeDescription": None,
    "hasTooMany": False,
    "item": {
        "$id": "2",
        "$values": [
            {
                "$id": "3",
                "installation": INSTALLATION_NUMBER,
                "documentNumber": "100001",
                "documentType": "Фактура",
                "documentDate": "2026-09-30T00:00:00",
                "dueDate": "2026-10-31T00:00:00",
                "amount": 40.10,
                "interest": 0,
                "total": 40.10,
                "paid": 40.10,
                "due": 0,
                "currency": "EUR",
                "hasInterest": False,
                "hasDocument": True,
            },
            {
                "$id": "4",
                "installation": INSTALLATION_NUMBER,
                "documentNumber": "100002",
                "documentType": "Фактура",
                "documentDate": "2026-10-31T00:00:00",
                "dueDate": "2026-11-30T00:00:00",
                "amount": 55.5,
                "interest": 0.25,
                "total": 55.75,
                "paid": 0,
                "due": 55.75,
                "currency": "EUR",
                "hasInterest": True,
                "hasDocument": True,
            },
        ],
    },
}

CONSUMPTION = {
    "$id": "1",
    "$values": [
        {"$id": "2", "installationNumber": INSTALLATION_NUMBER, "hotWaterEnergy": 0.12, "heatingEnergy": 0.0, "year": 2026, "month": 9, "isRealConsumption": True},
        {"$id": "3", "installationNumber": INSTALLATION_NUMBER, "hotWaterEnergy": 0.15, "heatingEnergy": 0.4, "year": 2026, "month": 10, "isRealConsumption": False},
        {"$id": "4", "installationNumber": INSTALLATION_NUMBER, "hotWaterEnergy": 0, "heatingEnergy": 0, "year": 2026, "month": 11, "isRealConsumption": False},
    ],
}

ACCIDENTS = {
    "$id": "1",
    "$values": [
        {
            "$id": "2",
            "name": "Авария на топлопровод",
            "geolocationSerialized": None,
            "type": 0,
            "status": 1,
            "fromDate": "2026-11-24T08:00:00+02:00",
            "untilDate": "2099-01-01T18:00:00+02:00",
            "affectedService": "Отопление и топла вода",
            "region": "Лозенец",
            "addresses": "ул. Тест 1-15",
            "locally": False,
        },
        {
            "$id": "3",
            "name": "Ремонт",
            "type": 1,
            "status": 2,
            "fromDate": "2026-11-01T08:00:00+02:00",
            "untilDate": "2026-11-02T18:00:00+02:00",
            "region": "Младост",
            "addresses": "бл. 5",
        },
    ],
}


def mock_api(aioclient_mock, *, login: dict | None = None, login_status: int = 200) -> None:
    """Register happy-path responses for every endpoint."""
    aioclient_mock.post(f"{API_URL}api/auth/login", json=login or auth_ok(), status=login_status)
    aioclient_mock.post(f"{API_URL}api/auth/refreshToken", json=auth_ok(refresh="refresh-2"))
    aioclient_mock.get(f"{API_URL}api/installations/getbyuserid", json=INSTALLATIONS)
    aioclient_mock.get(f"{API_URL}api/invoices/getAll", json=INVOICES)
    aioclient_mock.get(f"{API_URL}api/energyConsumption/get", json=CONSUMPTION)
    aioclient_mock.get(f"{API_URL}api/accidents/get", json=ACCIDENTS)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Enable custom_components and a recorder (a manifest dependency) in every test.

    recorder_mock must be requested before hass, which enable_custom_integrations uses.
    """
    yield
