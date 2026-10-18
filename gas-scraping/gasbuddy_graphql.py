#!/usr/bin/env python3
"""One-shot GasBuddy GraphQL experiment using its public website session flow.

Standard library only; no login, polling, pagination, or challenge bypasses.
Working technical access does not grant a data-reuse license.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import http.cookiejar
import json
import math
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
HOME_URL = "https://www.gasbuddy.com/"
ENDPOINT = HOME_URL + "graphql"
USER_AGENT = "RouteUniversityPrototype/0.1 (educational fuel-price research)"
MAX_BYTES = 4_000_000
HEADER_ALLOWLIST = {"content-type", "retry-after", "cf-mitigated", "server"}
QUERY = """query LocationBySearchTerm($search: String) {
  locationBySearchTerm(search: $search) {
    displayName countryCode latitude longitude
    stations {
      count
      cursor { next }
      results {
        id name latitude longitude fuels
        address { line1 locality region postalCode country }
        prices {
          fuelProduct
          cash { price postedTime }
          credit { price postedTime }
        }
      }
    }
    trends { areaName country today todayLow }
  }
}"""


def now_utc():
    return datetime.now(timezone.utc)


def request_response(opener, request, timeout):
    """Retain only diagnostic headers; never serialize session cookies or CSRF."""
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("Response exceeds 4 MB limit")
        return {
            "http_status": response.code,
            "headers": {k.lower(): v for k, v in response.headers.items() if k.lower() in HEADER_ALLOWLIST},
            "body": body.decode("utf-8", errors="replace"),
        }


def classify(response):
    status, body = response["http_status"], response["body"]
    if response["headers"].get("cf-mitigated", "").lower() == "challenge":
        return "challenge", None
    if status == 429:
        return "rate_limited", None
    if status in (401, 403):
        return "access_denied", None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return ("request_rejected" if status >= 400 else "unexpected_response"), None
    if not isinstance(payload, dict):
        return "unexpected_response", None
    if payload.get("errors"):
        return "graphql_error", payload
    location = (payload.get("data") or {}).get("locationBySearchTerm")
    if 200 <= status < 300 and isinstance(location, dict) and isinstance(location.get("stations"), dict):
        if isinstance(location["stations"].get("results"), list):
            return "success", payload
    return ("request_rejected" if status >= 400 else "unexpected_response"), payload


def probe(search, *, legacy=False, timeout=25, opener=None):
    """Make at most one homepage GET and one query POST. Stop on a denied GET."""
    if opener is None:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    report = {
        "checked_at": now_utc().isoformat(timespec="seconds"), "endpoint": ENDPOINT,
        "search": search, "mode": "legacy" if legacy else "website_session",
        "status": "not_run", "requests": [],
    }
    try:
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"}
        if not legacy:
            start = time.monotonic()
            response = request_response(opener, urllib.request.Request(HOME_URL, headers={"User-Agent": USER_AGENT}), timeout)
            report["requests"].append({"stage": "homepage", **{k: v for k, v in response.items() if k != "body"}})
            if response["http_status"] != 200 or response["headers"].get("cf-mitigated") == "challenge":
                report["status"], _ = classify(response)
                report["message"] = "Homepage session unavailable; no GraphQL POST was sent."
                return report, None
            match = re.search(r'window\.gbcsrf\s*=\s*("(?:[^"\\]|\\.)*")', response["body"])
            if not match:
                report["status"] = "bootstrap_changed"
                report["message"] = "Public website session token not found; no GraphQL POST was sent."
                return report, None
            # These headers match the public client flow observed on 2026-10-18.
            # Cookies stay in memory in CookieJar; gbcsrf is never written to disk.
            headers.update({"gbcsrf": json.loads(match.group(1)), "Apollo-Require-Preflight": "true",
                            "Origin": HOME_URL.rstrip("/"), "Referer": HOME_URL})
            time.sleep(max(0, 1.5 - (time.monotonic() - start)))
        request = urllib.request.Request(
            ENDPOINT, method="POST", headers=headers,
            data=json.dumps({"operationName": "LocationBySearchTerm", "variables": {"search": search}, "query": QUERY}).encode(),
        )
        response = request_response(opener, request, timeout)
        report["requests"].append({"stage": "graphql", **{k: v for k, v in response.items() if k != "body"}})
        report["status"], payload = classify(response)
        if report["status"] != "success":
            if payload is not None:
                report["graphql_errors"] = payload.get("errors", [])
            elif response["http_status"] >= 400:
                # Cloudflare HTML may contain session identifiers. Keep only a
                # short plaintext reason; never save arbitrary challenge pages.
                if "html" not in response["headers"].get("content-type", "") and "<" not in response["body"]:
                    report["response_excerpt"] = response["body"][:400]
            report["message"] = "Query did not return station data. No automatic retry was attempted."
        return report, payload
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        report["status"] = "network_error" if isinstance(error, (urllib.error.URLError, TimeoutError, OSError)) else "invalid_response"
        report["message"] = str(error)
        return report, None


def normalize(payload, *, collected_at, max_age_hours=24, limit=20):
    location = payload["data"]["locationBySearchTerm"]
    result = {"collected_at": collected_at, "search_area": location.get("displayName"),
              "reported_total": location["stations"].get("count"),
              "returned_on_page": len(location["stations"]["results"]),
              "has_more_pages": bool((location["stations"].get("cursor") or {}).get("next")),
              "scope": "First search-result page only. A city search can include surrounding states.",
              "stale_after_hours": max_age_hours, "trends": location.get("trends", []),
              "stations": [], "skipped_non_us": 0, "skipped_invalid_coordinates": 0}
    collected = datetime.fromisoformat(collected_at.replace("Z", "+00:00"))
    for item in location["stations"]["results"][:limit]:
        address = item.get("address") or {}
        if address.get("country") != "US":
            result["skipped_non_us"] += 1
            continue
        try:
            lat, lon = float(item["latitude"]), float(item["longitude"])
            if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("Invalid coordinates")
        except (ValueError, TypeError, KeyError):
            result["skipped_invalid_coordinates"] += 1
            continue
        observations = []
        for fuel in item.get("prices") or []:
            # Match by explicit fuelProduct, never array position.
            if not fuel.get("fuelProduct"):
                continue
            for payment in ("cash", "credit"):
                observation = fuel.get(payment) or {}
                try:
                    price = Decimal(str(observation.get("price")))
                    if not price.is_finite() or not Decimal("0.50") <= price <= Decimal("15"):
                        continue
                except (InvalidOperation, ValueError):
                    continue
                posted = observation.get("postedTime")
                age, freshness = None, "unknown"
                if posted:
                    try:
                        reported = datetime.fromisoformat(posted.replace("Z", "+00:00"))
                        if reported.tzinfo is None:
                            raise ValueError("Timestamp lacks timezone")
                        age = round((collected - reported).total_seconds() / 3600, 2)
                        freshness = "future_timestamp" if age < 0 else "stale" if age > max_age_hours else "recent"
                    except (ValueError, TypeError):
                        pass
                observations.append({"fuel_grade": fuel["fuelProduct"], "payment_type": payment,
                                     "price": format(price.quantize(Decimal("0.001")), "f"),
                                     "reported_at": posted, "age_hours": age, "freshness": freshness})
        result["stations"].append({"id": str(item["id"]), "name": item.get("name", ""),
                                   "address": address, "latitude": lat, "longitude": lon,
                                   "source_url": f"https://www.gasbuddy.com/station/{item['id']}",
                                   "currency": "USD", "unit": "US gallon", "prices": observations})
    return result


def write_outputs(output, report, data):
    output.mkdir(parents=True, exist_ok=True)
    # Always replace all three outputs so a failed run cannot leave apparent
    # success data from a previous run next to a new error report.
    (output / "diagnostics.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "stations.json").write_text(json.dumps(data, indent=2) + "\n")
    columns = ["station_id", "name", "street", "city", "state", "latitude", "longitude",
               "fuel_grade", "payment_type", "price", "currency", "unit", "reported_at",
               "age_hours", "freshness", "collected_at", "source_url"]
    with (output / "prices.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for station in (data or {}).get("stations", []):
            for observation in station["prices"]:
                writer.writerow({"station_id": station["id"], "name": station["name"],
                                 "street": station["address"].get("line1"), "city": station["address"].get("locality"),
                                 "state": station["address"].get("region"), "latitude": station["latitude"],
                                 "longitude": station["longitude"], "currency": station["currency"], "unit": station["unit"],
                                 "collected_at": data["collected_at"], "source_url": station["source_url"], **observation})


def print_results(report, data):
    print(f"Status: {report['status']}")
    for request in report["requests"]:
        print(f"  {request['stage']}: HTTP {request['http_status']}")
    if data is None:
        print(report.get("message", "No station data returned."))
        for error in report.get("graphql_errors", []):
            print(f"  {error.get('message', error)}")
        return
    print(f"\n{data['search_area']}: {data['reported_total']} reported matches; "
          f"{data['returned_on_page']} on first page; {len(data['stations'])} exported.")
    print("Broader city searches may include nearby states. No pagination is performed.\n")
    print(f"{'STATION':24} {'CITY / STATE':24} {'REG CREDIT':>10} {'REG CASH':>10}  REPORT AGE")
    for station in data["stations"]:
        regular = {p["payment_type"]: p for p in station["prices"] if p["fuel_grade"] == "regular_gas"}
        chosen = regular.get("credit") or regular.get("cash")
        age = f"{chosen['age_hours']:.1f}h ({chosen['freshness']})" if chosen and chosen["age_hours"] is not None else "unknown"
        credit = "$" + regular["credit"]["price"] if "credit" in regular else "—"
        cash = "$" + regular["cash"]["price"] if "cash" in regular else "—"
        place = f"{station['address'].get('locality', '')}, {station['address'].get('region', '')}"
        print(f"{station['name'][:24]:24} {place[:24]:24} {credit:>10} {cash:>10}  {age}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search", default="Philadelphia, PA", help="City/state or ZIP code (default: Philadelphia, PA)")
    parser.add_argument("--limit", type=int, default=20, help="Maximum records to export from the first page (1–20)")
    parser.add_argument("--max-age-hours", type=float, default=24, help="Label older price reports stale; retain them in exports")
    parser.add_argument("--legacy", action="store_true", help="Compare a direct POST without the current session setup")
    parser.add_argument("--timeout", type=float, default=25)
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "gasbuddy")
    args = parser.parse_args()
    if not args.search.strip():
        parser.error("--search cannot be empty")
    if not 1 <= args.limit <= 20:
        parser.error("--limit must be between 1 and 20")
    for name in ("max_age_hours", "timeout"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive and finite")
    report, payload = probe(args.search.strip(), legacy=args.legacy, timeout=args.timeout)
    data = None
    if report["status"] == "success":
        data = normalize(payload, collected_at=report["checked_at"], max_age_hours=args.max_age_hours, limit=args.limit)
    write_outputs(args.output, report, data)
    print_results(report, data)
    print(f"\nSaved diagnostics, stations.json and prices.csv to {args.output.resolve()}")
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
