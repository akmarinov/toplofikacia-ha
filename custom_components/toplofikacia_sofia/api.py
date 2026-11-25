"""Async client for the Toplofikatsia Sofia customer portal API (api.toplo.bg).

The API is the backend of https://my.toplo.bg (a Blazor WebAssembly app) and the
"Моето топло" mobile app. It is not publicly documented; the contract here was
derived from the portal's client code.

This module has no Home Assistant dependencies so it can be used standalone.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_URL = "https://api.toplo.bg/"
CLIENT_NAME = "MyPortal.Web"
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)

_CLAIM_USER_ID = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier"
# Refresh a little before the access token actually expires.
_TOKEN_EXPIRY_MARGIN = 60

# Values accepted by api/invoices/getAll?invoicePeriodFilter=
INVOICE_PERIOD_6_MONTHS = "SixMonths"
INVOICE_PERIOD_18_MONTHS = "EighteenMonths"
INVOICE_PERIOD_36_MONTHS = "ThirtySixMonths"

ACCIDENT_TYPE_ACCIDENT = 0
ACCIDENT_TYPE_REPAIRS = 1
ACCIDENT_STATUS_NEW = 0
ACCIDENT_STATUS_IN_PROGRESS = 1
ACCIDENT_STATUS_RESOLVED = 2


class ToploError(Exception):
    """Base error for the Toplofikatsia API."""


class ToploAuthError(ToploError):
    """Credentials were rejected or the session could not be renewed."""


class ToploConnectionError(ToploError):
    """The API could not be reached or returned an unexpected response."""


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Installation:
    """A heating installation ("абонатен номер") linked to the account."""

    id: str
    number: int
    name: str | None
    owner: str | None
    address: str | None
    contract_number: str | None
    station_number: int | None
    is_deactivated: bool
    latitude: float | None
    longitude: float | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Installation:
        return cls(
            id=str(data["id"]),
            number=int(data["number"]),
            name=data.get("name"),
            owner=data.get("owner"),
            address=data.get("address"),
            contract_number=data.get("contractNumber"),
            station_number=data.get("stationNumber"),
            is_deactivated=bool(data.get("isDeactivated", False)),
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
        )


@dataclass(frozen=True, slots=True)
class Invoice:
    """An invoice or other billing document from SAP."""

    installation: int
    document_number: str
    document_type: str | None
    document_date: datetime | None
    due_date: datetime | None
    amount: Decimal
    interest: Decimal
    total: Decimal
    paid: Decimal
    due: Decimal
    currency: str | None
    has_document: bool

    @property
    def is_paid(self) -> bool:
        return self.due == 0

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Invoice:
        return cls(
            installation=int(data["installation"]),
            document_number=str(data.get("documentNumber") or ""),
            document_type=data.get("documentType"),
            document_date=parse_datetime(data.get("documentDate")),
            due_date=parse_datetime(data.get("dueDate")),
            amount=_decimal(data.get("amount")),
            interest=_decimal(data.get("interest")),
            total=_decimal(data.get("total")),
            paid=_decimal(data.get("paid")),
            due=_decimal(data.get("due")),
            currency=data.get("currency"),
            has_document=bool(data.get("hasDocument", False)),
        )


@dataclass(frozen=True, slots=True)
class Consumption:
    """Monthly energy consumption for an installation, in MWh."""

    installation_number: int
    year: int
    month: int
    heating: Decimal
    hot_water: Decimal
    is_real: bool

    @property
    def total(self) -> Decimal:
        return self.heating + self.hot_water

    @property
    def period(self) -> date:
        return date(self.year, self.month, 1)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Consumption:
        return cls(
            installation_number=int(data.get("installationNumber") or 0),
            year=int(data["year"]),
            month=int(data["month"]),
            heating=_decimal(data.get("heatingEnergy")),
            hot_water=_decimal(data.get("hotWaterEnergy")),
            is_real=bool(data.get("isRealConsumption", False)),
        )


@dataclass(frozen=True, slots=True)
class Accident:
    """A network accident or planned repair."""

    name: str | None
    type: int
    status: int
    from_date: datetime | None
    until_date: datetime | None
    affected_service: str | None
    region: str | None
    addresses: str | None

    @property
    def is_repair(self) -> bool:
        return self.type == ACCIDENT_TYPE_REPAIRS

    def is_active(self, now: datetime | None = None) -> bool:
        if self.status == ACCIDENT_STATUS_RESOLVED:
            return False
        now = now or datetime.now(timezone.utc)
        if self.from_date and self.from_date > now:
            return False
        return not (self.until_date and self.until_date < now)

    def matches(self, keywords: list[str]) -> bool:
        if not keywords:
            return True
        haystack = " ".join(
            filter(None, (self.name, self.region, self.addresses))
        ).casefold()
        return any(k.casefold() in haystack for k in keywords)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Accident:
        return cls(
            name=data.get("name"),
            type=int(data.get("type") or 0),
            status=int(data.get("status") or 0),
            from_date=parse_datetime(data.get("fromDate")),
            until_date=parse_datetime(data.get("untilDate")),
            affected_service=data.get("affectedService"),
            region=data.get("region"),
            addresses=data.get("addresses"),
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    return Decimal(str(value))


def parse_datetime(value: str | None) -> datetime | None:
    """Parse a .NET (Newtonsoft) date string. Naive values are taken as UTC."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # .NET can emit 7 fractional digits; Python accepts at most 6.
    if "." in text:
        head, _, rest = text.partition(".")
        frac = rest
        tz = ""
        for sep in ("+", "-"):
            if sep in rest:
                frac, _, tail = rest.partition(sep)
                tz = sep + tail
                break
        text = f"{head}.{frac[:6]}{tz}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        _LOGGER.debug("Unparseable date from API: %s", value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def unwrap(payload: Any) -> Any:
    """Strip Newtonsoft reference-preservation metadata.

    The API serializes with PreserveReferencesHandling, so lists arrive as
    ``{"$id": "1", "$values": [...]}`` and repeated objects may appear as
    ``{"$ref": "3"}``.
    """
    seen: dict[str, Any] = {}

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return seen.get(str(node["$ref"]))
        node_id = node.get("$id")
        if "$values" in node:
            result: Any = []
            if node_id is not None:
                seen[str(node_id)] = result
            result.extend(walk(item) for item in node["$values"])
            return result
        obj: dict[str, Any] = {}
        if node_id is not None:
            seen[str(node_id)] = obj
        for key, val in node.items():
            if not key.startswith("$"):
                obj[key] = walk(val)
        return obj

    return walk(payload)


def decode_jwt_claims(token: str) -> dict[str, Any]:
    """Decode the (unverified) claims of a JWT."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError) as err:
        raise ToploAuthError("Malformed access token") from err


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class ToploClient:
    """Client for api.toplo.bg."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        *,
        base_url: str = API_URL,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._base_url = base_url.rstrip("/") + "/"
        self._token: str | None = None
        self._refresh_token: str | None = None
        self._token_exp: float = 0
        self._user_id: str | None = None
        self._auth_lock = asyncio.Lock()

    @property
    def user_id(self) -> str | None:
        return self._user_id

    # -- auth --------------------------------------------------------------- #

    async def login(self) -> None:
        """Log in with email and password."""
        result = await self._request(
            "POST",
            "api/auth/login",
            json_body={"email": self._email, "password": self._password},
            auth=False,
            allow_error_status=True,
        )
        self._store_auth_result(result, "Login failed")

    async def _refresh(self) -> bool:
        """Renew the access token with the refresh token. Returns success."""
        if not self._refresh_token or not self._user_id:
            return False
        try:
            result = await self._request(
                "POST",
                "api/auth/refreshToken",
                json_body={
                    "userId": self._user_id,
                    "refreshToken": self._refresh_token,
                },
                auth=False,
                allow_error_status=True,
            )
            self._store_auth_result(result, "Token refresh failed")
        except ToploAuthError as err:
            _LOGGER.debug("Refresh failed, will log in again: %s", err)
            return False
        return True

    def _store_auth_result(self, result: Any, context: str) -> None:
        if not isinstance(result, dict) or "isSuccess" not in result:
            raise ToploConnectionError(f"{context}: unexpected response")
        if not result.get("isSuccess"):
            errors = result.get("errors")
            message = "; ".join(errors) if errors else "no details"
            raise ToploAuthError(f"{context}: {message}")
        token = result.get("token")
        if not token:
            raise ToploAuthError(f"{context}: no token returned")
        claims = decode_jwt_claims(token)
        self._token = token
        self._refresh_token = result.get("refreshToken") or self._refresh_token
        self._token_exp = float(claims.get("exp") or 0)
        self._user_id = str(claims.get(_CLAIM_USER_ID) or claims.get("sub") or "")
        if not self._user_id:
            raise ToploAuthError(f"{context}: token has no user id")

    def _token_valid(self) -> bool:
        return bool(self._token) and time.time() < self._token_exp - _TOKEN_EXPIRY_MARGIN

    async def _ensure_token(self, force: bool = False) -> str:
        async with self._auth_lock:
            if force or not self._token_valid():
                if not await self._refresh():
                    await self.login()
            assert self._token is not None
            return self._token

    # -- transport ---------------------------------------------------------- #

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        auth: bool = True,
        allow_error_status: bool = False,
        _retried: bool = False,
    ) -> Any:
        headers = {"Accept": "application/json", "X-Client-Name": CLIENT_NAME}
        if auth:
            headers["Authorization"] = f"Bearer {await self._ensure_token()}"
        url = self._base_url + path
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
            ) as resp:
                if resp.status == 401 and auth and not _retried:
                    await self._ensure_token(force=True)
                    return await self._request(
                        method,
                        path,
                        params=params,
                        json_body=json_body,
                        auth=auth,
                        allow_error_status=allow_error_status,
                        _retried=True,
                    )
                if resp.status == 401:
                    raise ToploAuthError(f"Unauthorized: {path}")
                text = await resp.text()
                # Auth endpoints answer bad credentials with 4xx + an AuthResult body.
                if resp.status >= 500 or (resp.status >= 400 and not allow_error_status):
                    raise ToploConnectionError(
                        f"{method} {path} returned HTTP {resp.status}"
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise ToploConnectionError(f"{method} {path} failed: {err}") from err

        if not text:
            return None
        try:
            return unwrap(json.loads(text))
        except ValueError as err:
            raise ToploConnectionError(f"{method} {path}: invalid JSON") from err

    async def _sap_request(self, path: str, params: dict[str, Any]) -> Any:
        """Call an endpoint that wraps its payload in an SAPResponseDto."""
        result = await self._request("GET", path, params=params)
        if not isinstance(result, dict):
            raise ToploConnectionError(f"{path}: unexpected response shape")
        code = result.get("responseCode", 0)
        if code != 0:
            raise ToploConnectionError(
                f"{path}: SAP error {code} {result.get('responseCodeDescription') or ''}".strip()
            )
        return result.get("item")

    # -- endpoints ---------------------------------------------------------- #

    async def get_installations(self) -> list[Installation]:
        """Installations linked to the account."""
        await self._ensure_token()
        result = await self._request(
            "GET", "api/installations/getbyuserid", params={"userId": self._user_id}
        )
        return [Installation.from_api(item) for item in result or []]

    async def get_invoices(
        self,
        installation_numbers: list[int],
        period: str = INVOICE_PERIOD_18_MONTHS,
    ) -> list[Invoice]:
        """Invoices for the given installation numbers over a period."""
        if not installation_numbers:
            return []
        item = await self._sap_request(
            "api/invoices/getAll",
            {
                "installationIds": ",".join(str(n) for n in installation_numbers),
                "invoicePeriodFilter": period,
            },
        )
        return [Invoice.from_api(row) for row in item or []]

    async def get_consumption(
        self, installation_id: str, start: date, end: date
    ) -> list[Consumption]:
        """Monthly consumption for an installation between two dates."""
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        result = await self._request(
            "GET",
            "api/energyConsumption/get",
            params={
                "installationId": installation_id,
                "fromDate": datetime(start.year, start.month, start.day).strftime(fmt),
                "toDate": datetime(end.year, end.month, end.day).strftime(fmt),
            },
        )
        rows = [Consumption.from_api(row) for row in result or []]
        return sorted(rows, key=lambda c: (c.year, c.month))

    async def get_accidents(self, culture: str = "bg-bg") -> list[Accident]:
        """Current accidents and planned repairs across the network."""
        result = await self._request(
            "GET", "api/accidents/get", params={"culture": culture}
        )
        return [Accident.from_api(row) for row in result or []]
