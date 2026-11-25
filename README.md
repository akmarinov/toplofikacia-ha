# Toplofikatsia Sofia for Home Assistant

A custom integration for [Топлофикация София](https://toplo.bg) district heating. It signs in
with your **my.toplo.bg / Моето топло** account and shows balance, invoices, monthly heat
consumption and network accidents in Home Assistant.

It talks to `https://api.toplo.bg`, the undocumented backend that the customer portal and
mobile app use. The contract was worked out from the portal's client code, so it can break
if Toplofikatsia changes the API.

## What you get

For each active installation (абонатен номер) linked to your account:

| Entity | Notes |
| --- | --- |
| Balance due | Sum of what's still owed across invoices from the last 18 months, in the invoice currency (EUR). A list of unpaid documents is in the attributes. |
| Unpaid invoices | Count |
| Next due date | Earliest due date among unpaid documents |
| Latest invoice | Total of the most recent document, with number, dates, interest and paid amount in the attributes |
| Heating energy (last month) | MWh, latest month with data. `is_real_reading` is false for estimated months. |
| Hot water energy (last month) | MWh |
| Overdue payment (binary) | On when an unpaid document is past its due date |

For each substation (абонатна станция) serving your installations, live readings from the
Monitoring page, refreshed every 10 minutes:

| Entity | Notes |
| --- | --- |
| Outside temperature | °C, from the station's sensor |
| Heating temperature / Heating target temperature | °C, measured and set point of the heating circuit |
| Hot water temperature / Hot water target temperature | °C |
| Supply / Return temperature | °C at the station's heat meter |
| Flow | m³/h |
| Power | kW |
| Heat meter energy | MWh, cumulative for the whole station (not your apartment) |
| Supply / Return pressure, Indoor temperature | Only added if the station reports them |
| Unread alarms, Last measurement | Diagnostic |
| Heating on (binary) | On when the heating circuit is above 30 °C, meaning Toplofikatsia is supplying heat. Use it to tell when the heating season starts or stops, or to catch an outage. |

For the account:

| Entity | Notes |
| --- | --- |
| Active accidents and repairs | Count of active events, with details in the attributes. You can filter it by keywords. |
| Outage in my area (binary) | Added only when you set keywords. On when an active event matches them. |

### Long-term statistics / Energy dashboard

Monthly heating and hot-water consumption is written as external statistics:

- `toplofikacia_sofia:heating_<installation number>`
- `toplofikacia_sofia:hot_water_<installation number>`

Add them in **Settings → Dashboards → Energy** (as grid consumption, or as an individual
device), or show them in a Statistics graph card with the period set to month. Every refresh
rewrites the last ~2 years of months, so estimates get replaced by real readings when
Toplofikatsia revises them.

## Install

**HACS (custom repository):** HACS → ⋮ → Custom repositories → add this repository's URL as an
*Integration*, install **Toplofikatsia Sofia**, then restart Home Assistant.

**Manual:** copy `custom_components/toplofikacia_sofia` into your HA `config/custom_components/`
folder and restart.

Then go to **Settings → Devices & services → Add integration → Toplofikatsia Sofia** and enter
your my.toplo.bg email and password. You need an existing portal account with your
installations already added (adding an installation needs a captcha, so do that on the site).

### Options

**Configure** on the integration lets you set comma-separated **keywords** (for example
`Лозенец, Черни връх, АС 4567`). They are matched against each accident's name, region and
addresses.

## Polling

- Installations, invoices and consumption: every 6 hours
- Accidents and repairs: every 30 minutes
- Substation readings: every 10 minutes

## Trying the API without Home Assistant

```bash
python3 -m venv .venv && .venv/bin/pip install aiohttp
.venv/bin/python scripts/toplo_cli.py you@example.com --raw       # everything
.venv/bin/python scripts/toplo_cli.py you@example.com --stations  # live readings only
```

It asks for your password (or reads `TOPLO_PASSWORD`) and prints installations, invoices,
consumption and active accidents.

## Development

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest
```

## API reference (what the integration uses)

Base URL `https://api.toplo.bg/`. JSON uses camelCase, and lists arrive wrapped as
`{"$id": …, "$values": [...]}` (Newtonsoft reference preservation). Authenticated calls send
`Authorization: Bearer <token>` and `X-Client-Name: MyPortal.Web`.

| Call | Purpose |
| --- | --- |
| `POST api/auth/login` `{email, password}` | Returns `{token, refreshToken, isSuccess, errors}`. The JWT's `nameidentifier` claim is the user id. |
| `POST api/auth/refreshToken` `{userId, refreshToken}` | Same response shape |
| `GET api/installations/getbyuserid?userId=` | Installations (`id` GUID, `number`, `name`, `address`, `isDeactivated`, …) |
| `GET api/invoices/getAll?installationIds=1,2&invoicePeriodFilter=EighteenMonths` | `{responseCode, item: [...]}` with `documentNumber`, `documentDate`, `dueDate`, `total`, `paid`, `due`, `currency` |
| `GET api/energyConsumption/get?installationId=<guid>&fromDate=…Z&toDate=…Z` | Monthly `heatingEnergy` and `hotWaterEnergy` (MWh), plus `isRealConsumption` |
| `GET api/Stations/GetStationStandartView` | Live substation readings: `outsideTemperature`, `heatingMeasuredTemperature`, `domesticHotWaterMeasuredTemperature` and their set points, `heatmeterTEmitting`/`TReturning`, `heatmeterDebit`, `heatmeterPower`, `heatmeterEnergy`, `measuredDate` |
| `GET api/accidents/get?culture=bg-bg` | Accidents (`type` 0) and repairs (`type` 1), with `status` 0 new, 1 in progress, 2 resolved |

This project isn't affiliated with Топлофикация София ЕАД.
