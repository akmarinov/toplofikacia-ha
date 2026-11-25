#!/usr/bin/env python3
"""Quick manual check of the api.toplo.bg client, without Home Assistant.

Usage:
    python scripts/toplo_cli.py you@example.com            # prompts for password
    TOPLO_PASSWORD=... python scripts/toplo_cli.py you@example.com --raw

Only needs aiohttp (pip install aiohttp).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib.util
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

_API_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "toplofikacia_sofia"
    / "api.py"
)


def _load_api():
    # Load api.py directly so the HA-dependent package __init__ is not imported.
    spec = importlib.util.spec_from_file_location("toplo_api", _API_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["toplo_api"] = module
    spec.loader.exec_module(module)
    return module


async def _main(args: argparse.Namespace) -> int:
    import aiohttp

    api = _load_api()
    password = os.environ.get("TOPLO_PASSWORD") or getpass.getpass("Password: ")

    async with aiohttp.ClientSession() as session:
        client = api.ToploClient(session, args.email, password)
        try:
            await client.login()
        except api.ToploAuthError as err:
            print(err, file=sys.stderr)
            return 1
        print(f"Logged in, user id {client.user_id}")

        if args.raw:
            for path, params in (
                ("api/installations/getbyuserid", {"userId": client.user_id}),
                ("api/accidents/get", {"culture": "bg-bg"}),
            ):
                data = await client._request("GET", path, params=params)  # noqa: SLF001
                print(f"\n== {path}\n{json.dumps(data, ensure_ascii=False, indent=2, default=str)}")

        if args.annual:
            for inst in [i for i in await client.get_installations() if not i.is_deactivated]:
                for year in range(date.today().year - 3, date.today().year + 1):
                    raw = await client._request(  # noqa: SLF001
                        "GET",
                        "api/annualAligning/get",
                        params={"installationId": inst.id, "fromYear": year, "toYear": year},
                    )
                    print(f"#{inst.number} {year}: {json.dumps(raw, ensure_ascii=False, default=str)}")
            return 0

        if args.stations:
            data = await client._request("GET", "api/Stations/GetStationStandartView")  # noqa: SLF001
            print("\n== api/Stations/GetStationStandartView")
            if args.raw:
                print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
            # Same rule as the Heating on binary sensor.
            threshold = 30.0
            for st in await client.get_stations():
                loop = st.heating_temperature
                verdict = "unknown" if loop is None else ("ON" if loop >= threshold else "off")
                print(
                    f"  {st.name}: measured {st.measured}\n"
                    f"    heating loop {loop} °C (target {st.heating_set_temperature}), "
                    f"hot water {st.hot_water_temperature} °C (target {st.hot_water_set_temperature})\n"
                    f"    supply/return {st.supply_temperature}/{st.return_temperature} °C, "
                    f"flow {st.flow} L/h, power {st.power} kW, outside {st.outside_temperature} °C\n"
                    f"    Heating on: {verdict}"
                )
            return 0

        installations = await client.get_installations()
        print(f"\n{len(installations)} installation(s)")
        for inst in installations:
            print(f"  #{inst.number} {inst.name or ''} | {inst.address} | deactivated={inst.is_deactivated}")

        active = [i for i in installations if not i.is_deactivated]
        try:
            invoices = await client.get_invoices([i.number for i in active])
        except api.ToploError as err:
            print(f"\nInvoices failed: {err}")
            invoices = []
        print(f"\n{len(invoices)} invoice(s) in the last 18 months")
        for inv in sorted(invoices, key=lambda x: x.document_date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:12]:
            print(
                f"  {inv.installation} {inv.document_number} {inv.document_type} "
                f"date={inv.document_date and inv.document_date.date()} "
                f"due_date={inv.due_date and inv.due_date.date()} "
                f"total={inv.total} paid={inv.paid} due={inv.due} {inv.currency}"
            )

        today = date.today()
        for inst in active:
            rows = await client.get_consumption(inst.id, date(today.year - 1, 1, 1), date(today.year, 12, 31))
            print(f"\nConsumption #{inst.number} (MWh)")
            for row in rows:
                print(
                    f"  {row.year}-{row.month:02d} heating={row.heating} "
                    f"hot_water={row.hot_water} real={row.is_real}"
                )

        for inst in active:
            try:
                rows = await client.get_station_consumption(
                    inst.id, date(today.year - 1, 1, 1), date(today.year, 12, 31)
                )
            except api.ToploError as err:
                print(f"\nBuilding consumption #{inst.number} failed: {err}")
            else:
                print(f"\nBuilding consumption #{inst.number} (MWh)")
                for row in rows[-6:]:
                    print(f"  {row.year}-{row.month:02d} heating={row.heating} hot_water={row.hot_water} real={row.is_real}")
            try:
                corrections = await client.get_annual_corrections(inst.id, range(today.year - 3, today.year + 1))
            except api.ToploError as err:
                print(f"\nAnnual correction #{inst.number} failed: {err}")
            else:
                print(f"\nAnnual correction #{inst.number} (MWh)")
                for c in corrections:
                    print(
                        f"  {c.period_from and c.period_from.date()}..{c.period_to and c.period_to.date()} "
                        f"heating {c.heating_invoiced}->{c.heating_final} "
                        f"hot_water {c.hot_water_invoiced}->{c.hot_water_final} status={c.status}"
                    )

        accidents = await client.get_accidents()
        active_acc = [a for a in accidents if a.is_active()]
        print(f"\n{len(accidents)} accident/repair record(s), {len(active_acc)} active")
        for acc in active_acc[:10]:
            print(f"  [{'repair' if acc.is_repair else 'accident'}] {acc.name} | {acc.region} | until {acc.until_date}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("email")
    parser.add_argument("--raw", action="store_true", help="also dump raw JSON for some endpoints")
    parser.add_argument("--stations", action="store_true", help="only dump live substation readings")
    parser.add_argument("--annual", action="store_true", help="only dump annual corrections, year by year")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
