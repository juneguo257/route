#!/usr/bin/env python3
"""Collect public retailer fuel prices and generate a Philadelphia OSM map.

Python 3.10+, standard library only. No credentials or paid services required.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import partial
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
CENTER = (39.9526, -75.1652)
COSTCO_URL = "https://www.costco.com/AjaxWarehouseBrowseLookupView?hasGas=true&populateWarehouseDetails=true"
SAMS_CLUBS = (
    ("6332", "philadelphia", "pa"),
    ("6676", "willow-grove", "pa"),
    ("6693", "langhorne", "pa"),
    ("8145", "cinnaminson", "nj"),
)
USER_AGENT = "RouteUniversityPrototype/0.1 (educational fuel-price research)"


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def price_value(value):
    """Keep the fractional cent, rejecting absent or implausible USD prices."""
    try:
        price = Decimal(str(value))
        if price.is_finite() and Decimal("0.50") <= price <= Decimal("15"):
            return format(price.quantize(Decimal("0.001")), "f")
    except (InvalidOperation, ValueError):
        pass
    return None


def distance_miles(latitude, longitude):
    lat1, lat2 = map(math.radians, (CENTER[0], float(latitude)))
    dlat = lat2 - lat1
    dlon = math.radians(float(longitude) - CENTER[1])
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 3958.7613 * 2 * math.asin(math.sqrt(min(1, a)))


class PageScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and (attrs.get("id") == "__NEXT_DATA__" or attrs.get("type") == "application/ld+json"):
            self.current = {"attrs": attrs, "text": ""}

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.scripts.append(self.current)
            self.current = None


def station_record(*, identifier, brand, name, address, city, state, postal_code,
                   latitude, longitude, prices, source_url, collected_at, cache_used,
                   hours=None, source_record_created_at=None):
    lat, lon = float(latitude), float(longitude)
    if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Invalid station coordinates")
    if not prices:
        raise ValueError("No valid fuel prices")
    return {
        "id": identifier, "brand": brand, "name": name, "address": address,
        "city": city, "state": state, "postal_code": str(postal_code),
        "latitude": lat, "longitude": lon, "prices": prices,
        "currency": "USD", "unit": "US gallon", "membership_required": True,
        "distance_miles": round(distance_miles(lat, lon), 2),
        "source_url": source_url, "collected_at": collected_at,
        "price_updated_at": None,
        # Sam's metadata describes creation of its feed record, not a verified pump update.
        "source_record_created_at": source_record_created_at,
        "cache_used": cache_used, "fuel_hours": hours or [],
    }


def parse_costco(content, collected_at, cache_used=False):
    payload = json.loads(content)
    stations = {}
    seen_warehouses = 0
    for item in walk(payload):
        if "stlocID" not in item or "latitude" not in item:
            continue
        seen_warehouses += 1
        gas = item.get("gasPrices") or {}
        # Never attach another warehouse's prices to this station.
        if str(gas.get("warehouseid")) != str(item["stlocID"]):
            continue
        prices = {grade: price for grade in ("regular", "premium", "diesel")
                  if (price := price_value(gas.get(grade))) is not None}
        if not prices or item.get("country") != "US":
            continue
        identifier = str(item["stlocID"])
        record = station_record(
            identifier=f"costco-{identifier}", brand="Costco",
            name=f"Costco · {item.get('locationName') or item.get('city')}",
            address=item.get("address1", ""), city=item.get("city", ""),
            state=item.get("state", ""), postal_code=item.get("zipCode", ""),
            latitude=item["latitude"], longitude=item["longitude"], prices=prices,
            source_url=f"https://www.costco.com/warehouse-locations/warehouse-{identifier}.html",
            collected_at=collected_at, cache_used=cache_used,
            hours=[f"{h.get('title', '').strip()} {h.get('time', '')}" for h in item.get("gasStationHours", [])],
        )
        stations[record["id"]] = record
    if not seen_warehouses or not stations:
        raise ValueError("Costco response contains no recognized priced warehouses; the source may have changed")
    return list(stations.values())


def parse_sams(content, club_id, source_url, collected_at, cache_used=False):
    parser = PageScripts()
    parser.feed(content)
    next_data = next((json.loads(s["text"]) for s in parser.scripts
                      if s["attrs"].get("id") == "__NEXT_DATA__"), None)
    if not next_data:
        raise ValueError("Sam's page has no embedded price data; it may be blocked or its format changed")
    page = next_data["props"]["pageProps"]
    node = page.get("initialNodeDetail", {}).get("data", {}).get("nodeDetail", {})
    if str(node.get("id")) != str(club_id):
        raise ValueError("Sam's returned a different club; refusing to assign its prices")
    modules = page.get("initialTempoData", {}).get("contentLayout", {}).get("modules", [])
    fuel = next((v["storeFuelPrices"] for v in walk(modules) if v.get("storeFuelPrices")), {})
    if not fuel.get("prices"):
        raise ValueError("Retailer publishes no fuel prices for this club")
    if not str(fuel.get("id", "")).endswith("_" + str(club_id)):
        raise ValueError("Sam's price feed does not match requested club")
    grades = {"UNLEAD": "regular", "UNLEADED": "regular", "REGULAR": "regular",
              "MIDGRADE": "midgrade", "PREMIUM": "premium", "DIESEL": "diesel"}
    prices = {}
    for entry in fuel.get("prices", []):
        grade = grades.get(str(entry.get("name", "")).upper())
        price = price_value(entry.get("price"))
        if grade and price is not None:
            prices[grade] = price
    address = node.get("address", {})
    geo = node.get("geoPoint", {})
    fuel_service = next((s for s in node.get("services", [])
                         if s.get("name") in ("FUEL_CENTER", "FUEL", "GAS")), {})
    hours = [f"{h['day']}: {'Closed' if h.get('closed') else str(h.get('start')) + '–' + str(h.get('end'))}"
             for h in fuel_service.get("operationalHours", [])]
    return [station_record(
        identifier=f"sams-{club_id}", brand="Sam's Club", name=node.get("displayName", "Sam's Club"),
        address=address.get("addressLineOne", ""), city=address.get("city", ""),
        state=address.get("state", ""), postal_code=address.get("postalCode", ""),
        latitude=geo.get("latitude"), longitude=geo.get("longitude"), prices=prices,
        source_url=source_url, collected_at=collected_at, cache_used=cache_used,
        hours=hours, source_record_created_at=fuel.get("metadata", {}).get("dateCreated"),
    )]


class Fetcher:
    def __init__(self, cache_dir, *, refresh=False, offline=False):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.refresh, self.offline = refresh, offline
        self.last_request = {}

    def get(self, url):
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            cached = json.loads(path.read_text())
            age = datetime.now(timezone.utc) - datetime.fromisoformat(cached["collected_at"])
            if self.offline or (not self.refresh and 0 <= age.total_seconds() < 3600):
                return cached["content"], cached["collected_at"], True
        if self.offline:
            raise ValueError("No cached response; run once with internet access first")
        host = urllib.parse.urlsplit(url).netloc
        time.sleep(max(0, 1.5 - (time.monotonic() - self.last_request.get(host, 0))))
        self.last_request[host] = time.monotonic()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html"})
        # One request, no retries, browser impersonation, or challenge bypasses.
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise ValueError("Source response exceeds 8 MB limit")
            content = raw.decode("utf-8")
        collected_at = utc_now()
        path.write_text(json.dumps({"url": url, "collected_at": collected_at, "content": content}))
        return content, collected_at, False


def collect(fetcher, radius_miles, state=None):
    stations, sources = {}, []
    jobs = [("Costco", COSTCO_URL, parse_costco)]
    for club_id, city, region in SAMS_CLUBS:
        url = f"https://www.samsclub.com/club/{club_id}-{city}-{region}/fuel-center"
        jobs.append((f"Sam's Club #{club_id}", url, partial(parse_sams, club_id=club_id, source_url=url)))
    for name, url, parser in jobs:
        print(f"Collecting {name}…", flush=True)
        try:
            content, collected_at, cache_used = fetcher.get(url)
            records = parser(content=content, collected_at=collected_at, cache_used=cache_used)
            selected = [s for s in records if s["distance_miles"] <= radius_miles
                        and (state is None or s["state"] == state)]
            stations.update((s["id"], s) for s in selected)
            sources.append({"name": name, "url": url, "status": "ok", "stations": len(selected),
                            "collected_at": collected_at, "cache_used": cache_used})
            print(f"  {len(selected)} stations in area ({'cached' if cache_used else 'fetched'})", flush=True)
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, TypeError, OSError) as error:
            message = str(error)
            sources.append({"name": name, "url": url, "status": "error", "error": message})
            print(f"  unavailable: {message}", file=sys.stderr, flush=True)
    return {"generated_at": utc_now(), "center": {"latitude": CENTER[0], "longitude": CENTER[1]},
            "radius_miles": radius_miles, "state_filter": state,
            "coverage": "Costco and four selected Sam's Club locations only; not all Philadelphia stations.",
            "sources": sources, "stations": sorted(stations.values(), key=lambda s: (Decimal(s["prices"].get("regular", "99")), s["id"]))}


def write_outputs(data, output):
    output.mkdir(parents=True, exist_ok=True)
    (output / "stations.json").write_text(json.dumps(data, indent=2) + "\n")
    columns = ["id", "brand", "name", "address", "city", "state", "postal_code", "latitude", "longitude",
               "fuel_grade", "price", "currency", "unit", "membership_required", "distance_miles",
               "collected_at", "price_updated_at", "source_url"]
    with (output / "prices.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for station in data["stations"]:
            for grade, price in station["prices"].items():
                writer.writerow({**{c: station.get(c) for c in columns}, "fuel_grade": grade, "price": price})
    template = (ROOT / "web" / "index.html").read_text()
    safe_json = json.dumps(data).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    (output / "index.html").write_text(template.replace("__ROUTE_DATA__", safe_json))
    for asset in ("map.js", "data.js", "style.css"):
        shutil.copyfile(ROOT / "web" / asset, output / asset)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--radius-miles", type=float, default=30, help="Straight-line radius from Center City (default: 30)")
    parser.add_argument("--state", choices=("PA", "NJ", "DE"), help="Restrict results to one state")
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".route-cache")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--refresh", action="store_true", help="Fetch again instead of using the one-hour cache")
    mode.add_argument("--offline", action="store_true", help="Rebuild from cached responses without contacting retailers")
    parser.add_argument("--serve", action="store_true", help="Serve the generated map on localhost")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not math.isfinite(args.radius_miles) or not 0 < args.radius_miles <= 100:
        parser.error("--radius-miles must be greater than zero and at most 100")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    data = collect(Fetcher(args.cache_dir, refresh=args.refresh, offline=args.offline), args.radius_miles, args.state)
    write_outputs(data, args.output)
    print(f"\nSaved {len(data['stations'])} stations → {args.output.resolve() / 'index.html'}", flush=True)
    if args.serve:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(SimpleHTTPRequestHandler, directory=str(args.output.resolve())))
        print(f"Map: http://127.0.0.1:{args.port}  (Ctrl+C to stop)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    return 0 if data["stations"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
