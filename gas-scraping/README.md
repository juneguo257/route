# Route

A Python prototype for exporting gas prices near Philadelphia and showing saved observations on an interactive OpenStreetMap map.

## Run

Requires Python 3.10 or newer. No Python packages, API keys, or paid subscriptions are needed.

```sh
python3 serve_map.py
```

Run commands from this `gas-scraping/` directory. Open **http://127.0.0.1:8765**. The server displays saved exports; it does not collect prices. Stop with Ctrl+C.

The frontend defaults to `output/gasbuddy/stations.json`, reads it once on page load, and shows the saved collection time. After exporting new GasBuddy data, reload the browser to load the new file; no frontend rebuild or server restart is needed. A data-source selector also provides the earlier Costco/Sam's Club snapshot when present.

```sh
# Explicitly export new data, then reload the existing map tab
python3 gasbuddy_graphql.py --search "Philadelphia, PA"

# Use a ZIP search for a smaller area (replaces the saved GasBuddy export)
python3 gasbuddy_graphql.py --search 19104
```

The map filters by fuel grade, cash/credit payment basis, brand, and state, and sorts by price or distance. Cash mode only includes reported cash prices; it never substitutes a credit price. Popups retain both payment types with their individual report timestamps. Staleness is recalculated from report time when the page loads or filters change. Membership is only asserted when supplied by the selected source; GasBuddy's export does not include that field. Missing, invalid, or failed GasBuddy exports produce an unavailable state rather than silently showing retailer prices.

```sh
# Collect and export without starting a server
python3 gas_prices.py

# Limit to Pennsylvania stations within 20 straight-line miles of Center City
python3 gas_prices.py --radius-miles 20 --state PA --serve

# Rebuild using previous responses, without contacting retailers
python3 gas_prices.py --offline --serve

# Explicitly request fresh responses instead of using the one-hour cache
python3 gas_prices.py --refresh
```

The retailer collector's default area is a **30-mile radius from Center City Philadelphia**. GasBuddy coverage follows the exported search page, which can include other states. Selecting a station opens its prices and source link on the map. The CSV download follows the selected data source and contains its full export, irrespective of map filters.

## Sources and scope

- **Costco:** its public [warehouse locator JSON response](https://www.costco.com/AjaxWarehouseBrowseLookupView?hasGas=true&populateWarehouseDetails=true) supplies prices, coordinates, and fuel station hours. The collector checks that each price record's warehouse ID matches the location before keeping it, then filters by radius.
- **Sam's Club:** public fuel-center pages embed structured prices and station coordinates. This prototype queries four selected locations: [Philadelphia #6332](https://www.samsclub.com/club/6332-philadelphia-pa/fuel-center), [Willow Grove #6676](https://www.samsclub.com/club/6676-willow-grove-pa/fuel-center), [Langhorne #6693](https://www.samsclub.com/club/6693-langhorne-pa/fuel-center), and [Cinnaminson #8145](https://www.samsclub.com/club/8145-cinnaminson-nj/fuel-center). Add locations to `SAMS_CLUBS` in `gas_prices.py`.
- **Map:** Leaflet 1.9.4 and standard [OpenStreetMap tiles](https://operations.osmfoundation.org/policies/tiles/), with attribution. An internet connection is needed for the map library and tiles, even when retailer responses are cached.

These are undocumented website responses, not a supported open-data API. A page change or access block can stop a collector. The script identifies itself, spaces requests to each host, caches responses for one hour, and reports failures without trying to bypass blocks. There is no background scraping or OPIS integration.

The retailer snapshot has **partial coverage**, not a list of all Philadelphia stations. Both chains require membership to purchase fuel. Published prices may differ from the pump, and neither retailer collector supplies a verified price update timestamp or cash/credit payment basis. `collected_at` is when Route fetched the response, not when the retailer changed its price. Sam's feed-record creation time is retained separately and is not presented as price freshness. Missing grades remain missing. Prices preserve fractional cents (for example, `4.089` USD per US gallon).

Distances are straight-line miles from Center City, not road distance or detour time. Cross-river comparisons do not account for bridge tolls.

## Files

- `gas_prices.py`: collectors, normalization, cache, CLI, exports, and local server.
- `serve_map.py`: build frontend assets and serve saved exports without collection.
- `gasbuddy_graphql.py`: separate one-shot GasBuddy export script.
- `web/`: Leaflet map template, styles, and filters.
- `web/data.js`: adapters from each saved export into one map model.
- `output/index.html`: generated map.
- `output/stations.json`: station records, collection times, and source status.
- `output/prices.csv`: one row per station and fuel grade.
- `.route-cache/`: local responses; ignored by Git along with generated output.

The retailer snapshot is embedded in generated HTML. GasBuddy's snapshot is read from the local JSON file on page load. The frontend makes no requests to GasBuddy or retailer websites; its network requests are local assets/data, Leaflet, and OpenStreetMap tiles. Future on-demand route lookup can provide the same export schema through the data adapter.

## Checks

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_map_data.cjs
```

Tests use synthetic source responses and temporary directories; they make no network requests. Node is only needed for the JavaScript adapter tests, not to run the prototype.

## GasBuddy GraphQL experiment

The separate `gasbuddy_graphql.py` prototype checks GasBuddy's current public website request flow and exports one search-result page. On October 18, 2026, a direct POST using the older request format returned HTTP 400. Following the website's current session setup returned HTTP 200 with station coordinates, fuel grades, separate cash/credit prices, and report timestamps.

```sh
python3 gasbuddy_graphql.py --search "Philadelphia, PA"

# A ZIP search for a smaller local area
python3 gasbuddy_graphql.py --search 19104 --limit 10

# Compare the direct POST without session setup; use another output directory
python3 gasbuddy_graphql.py --legacy --output output/gasbuddy-legacy
```

The current client flow is one GET to the GasBuddy homepage, followed by one POST to `/graphql` using an in-memory cookie session, the public `window.gbcsrf` value, `Apollo-Require-Preflight`, and the website's Origin/Referer headers. The script keeps its own descriptive User-Agent. No login or API key is required for the observed response. It stops on denied access, rate limits, or challenges and makes no automatic retries. Session cookies and tokens are not written to disk.

Outputs go to `output/gasbuddy/`:

- `diagnostics.json`: request status and safe response metadata, including GraphQL errors if present.
- `stations.json`: normalized station records and per-grade, per-payment price observations; `null` on failure.
- `prices.csv`: one row per valid price observation; header only on failure.

Prices retain three decimal places. Zero/missing prices are omitted, fuel grades use the explicit `fuelProduct` field, and each observation keeps `reported_at`, `age_hours`, and a freshness label. Reports older than 24 hours are labeled stale, not silently discarded; customize with `--max-age-hours`. Collection time and report time remain separate. Only US stations are exported to avoid mixing USD/gallon prices with Canadian units.

**Coverage:** the city search includes surrounding states and returns a paginated result. This script exports at most the first 20 records, performs no pagination, and reports the broader total separately. `--limit` only limits the exported records. The existing map reads this export as its default data source.

This is a technical experiment with an undocumented endpoint, not a data license. GasBuddy's [current terms](https://help.gasbuddy.com/hc/en-us/articles/40439396627991-Updated-Terms-of-Service-Effective-June-22nd-2026), section 15, restrict automated extraction, database creation, and redistribution. Endpoint availability does not establish permission for ongoing use or publication.
