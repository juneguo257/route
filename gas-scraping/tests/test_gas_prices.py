import json
from pathlib import Path
import tempfile
import unittest

from gas_prices import COSTCO_URL, Fetcher, collect, parse_costco, parse_sams, price_value, write_outputs


OBSERVED = "2026-10-18T15:00:00+00:00"


def warehouse(identifier=248, price_id=248):
    return {"stlocID": identifier, "locationName": "Montgomeryville", "country": "US",
            "address1": "740 Upper State Rd", "city": "North Wales", "state": "PA", "zipCode": "19454",
            "latitude": 40.237, "longitude": -75.234,
            "gasPrices": {"warehouseid": str(price_id), "regular": "4.089", "premium": None}}


def sams_page(club="6332", fuel_club="6332"):
    node = {"id": club, "displayName": "Philadelphia Sam's Club", "address": {"city": "Philadelphia", "state": "PA"},
            "geoPoint": {"latitude": 40.092943, "longitude": -74.958548}}
    fuel = {"id": f"CPF_FUELPRICE_PROD_{fuel_club}", "metadata": {"dateCreated": OBSERVED},
            "prices": [{"name": "UNLEAD", "price": 4.119}, {"name": "DIESEL", "price": None}]}
    page = {"initialNodeDetail": {"data": {"nodeDetail": node}},
            "initialTempoData": {"contentLayout": {"modules": [{"configs": {"storeFuelPrices": fuel}}]}}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": page}}) + "</script>"


class CollectorTests(unittest.TestCase):
    def test_prices_keep_fractional_cent_and_reject_nonprices(self):
        self.assertEqual(price_value("4.089"), "4.089")
        for bad in (None, 0, -1, "NaN", "Infinity", "N/A", 999, True):
            self.assertIsNone(price_value(bad))

    def test_costco_identity_deduplication_and_missing_grades(self):
        records = parse_costco(json.dumps([False, warehouse(), warehouse(), warehouse(999, 248)]), OBSERVED)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["prices"], {"regular": "4.089"})
        self.assertIsNone(records[0]["price_updated_at"])

    def test_sams_extracts_fractional_cent_without_calling_metadata_price_update(self):
        station = parse_sams(sams_page(), "6332", "https://www.samsclub.com/club/6332", OBSERVED)[0]
        self.assertEqual(station["prices"], {"regular": "4.119"})
        self.assertIsNone(station["price_updated_at"])
        self.assertEqual(station["source_record_created_at"], OBSERVED)

    def test_sams_rejects_wrong_club_or_wrong_price_feed(self):
        for content in (sams_page(club="9999"), sams_page(fuel_club="9999"), "<h1>Access denied</h1>"):
            with self.assertRaises(ValueError):
                parse_sams(content, "6332", "https://www.samsclub.com/", OBSERVED)

    def test_empty_or_changed_costco_schema_is_an_error(self):
        for value in ([], {"message": "Access denied"}, [warehouse(248, 999)]):
            with self.assertRaises(ValueError):
                parse_costco(json.dumps(value), OBSERVED)

    def test_partial_failure_is_reported_and_geographic_filter_applies(self):
        class StubFetcher:
            def get(self, url):
                if url != COSTCO_URL:
                    raise ValueError("Source blocked")
                return json.dumps([warehouse()]), OBSERVED, True
        result = collect(StubFetcher(), radius_miles=30, state="PA")
        self.assertEqual(len(result["stations"]), 1)
        self.assertEqual(sum(s["status"] == "error" for s in result["sources"]), 4)
        self.assertFalse(collect(StubFetcher(), radius_miles=1)["stations"])

    def test_offline_never_fetches_and_exports_escape_script_termination(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                Fetcher(Path(directory) / "cache", offline=True).get(COSTCO_URL)
            record = parse_costco(json.dumps([warehouse()]), OBSERVED)[0]
            record["name"] = "</script><script>alert(1)</script>"
            result = {"generated_at": OBSERVED, "stations": [record], "sources": []}
            output = Path(directory) / "output"
            write_outputs(result, output)
            self.assertNotIn(record["name"], (output / "index.html").read_text())
            self.assertIn("4.089", (output / "prices.csv").read_text())
            self.assertEqual(json.loads((output / "stations.json").read_text())["stations"][0]["name"], record["name"])


if __name__ == "__main__":
    unittest.main()
