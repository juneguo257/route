from email.message import Message
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gasbuddy_graphql import classify, normalize, probe, write_outputs


COLLECTED = "2026-10-18T16:00:00+00:00"


def payload():
    return {"data": {"locationBySearchTerm": {
        "displayName": "Philadelphia", "stations": {"count": 1566, "cursor": {"next": "next-page"},
        "results": [{"id": "42", "name": "Test station", "latitude": 39.95, "longitude": -75.16,
                     "address": {"country": "US", "locality": "Philadelphia", "region": "PA"},
                     "prices": [
                         {"fuelProduct": "premium_gas", "credit": {"price": 4.899, "postedTime": "2026-10-16T16:00:00Z"}},
                         {"fuelProduct": "regular_gas", "cash": {"price": 3.899, "postedTime": None},
                          "credit": {"price": 3.999, "postedTime": "2026-10-18T15:00:00Z"}},
                         {"fuelProduct": "diesel", "credit": {"price": 0, "postedTime": None}},
                     ]}]}}}}


class FakeResponse(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body.encode())
        self.code = status
        self.headers = Message()
        for key, value in (headers or {"Content-Type": "application/json"}).items():
            self.headers[key] = value


class FakeOpener:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    def open(self, request, timeout):
        self.requests.append(request)
        return self.responses.pop(0)


class GasBuddyTests(unittest.TestCase):
    def test_uses_public_session_headers_without_saving_secret(self):
        opener = FakeOpener([FakeResponse('window.gbcsrf = "public-session-token";'), FakeResponse(json.dumps(payload()))])
        with patch("gasbuddy_graphql.time.sleep"):
            report, result = probe("19104", opener=opener)
        self.assertEqual(report["status"], "success")
        self.assertEqual(len(opener.requests), 2)
        headers = dict((k.lower(), v) for k, v in opener.requests[1].header_items())
        self.assertEqual(headers["gbcsrf"], "public-session-token")
        self.assertEqual(headers["apollo-require-preflight"], "true")
        self.assertIn("RouteUniversityPrototype", headers["user-agent"])
        self.assertNotIn("public-session-token", json.dumps(report))
        self.assertEqual(json.loads(opener.requests[1].data)["variables"], {"search": "19104"})

    def test_challenge_on_homepage_stops_before_post(self):
        opener = FakeOpener([FakeResponse("challenge", 403, {"cf-mitigated": "challenge"})])
        report, result = probe("19104", opener=opener)
        self.assertEqual(report["status"], "challenge")
        self.assertIsNone(result)
        self.assertEqual(len(opener.requests), 1)

    def test_rejection_and_graphql_errors_are_distinct_from_success(self):
        response = {"http_status": 400, "body": "Bad Request", "headers": {}}
        self.assertEqual(classify(response)[0], "request_rejected")
        response.update(http_status=200, body=json.dumps({"errors": [{"message": "Unknown field"}]}))
        self.assertEqual(classify(response)[0], "graphql_error")
        response.update(http_status=429)
        self.assertEqual(classify(response)[0], "rate_limited")

    def test_explicit_grade_cash_credit_precision_and_freshness(self):
        result = normalize(payload(), collected_at=COLLECTED)
        prices = result["stations"][0]["prices"]
        self.assertEqual(len(prices), 3)
        self.assertEqual(prices[0]["fuel_grade"], "premium_gas")
        self.assertEqual(prices[0]["freshness"], "stale")
        self.assertEqual(prices[1]["price"], "3.899")
        self.assertEqual(prices[1]["payment_type"], "cash")
        self.assertEqual(prices[1]["freshness"], "unknown")
        self.assertEqual(prices[2]["age_hours"], 1)
        self.assertTrue(result["has_more_pages"])

    def test_non_us_and_invalid_coordinates_are_never_exported_as_us_prices(self):
        value = payload()
        rows = value["data"]["locationBySearchTerm"]["stations"]["results"]
        rows[0]["address"]["country"] = "CA"
        self.assertFalse(normalize(value, collected_at=COLLECTED)["stations"])
        rows[0]["address"]["country"] = "US"
        rows[0]["latitude"] = float("nan")
        self.assertFalse(normalize(value, collected_at=COLLECTED)["stations"])

    def test_failure_replaces_previous_success_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = normalize(payload(), collected_at=COLLECTED)
            write_outputs(output, {"status": "success"}, result)
            self.assertIn("3.899", (output / "prices.csv").read_text())
            write_outputs(output, {"status": "access_denied"}, None)
            self.assertIsNone(json.loads((output / "stations.json").read_text()))
            self.assertEqual(len((output / "prices.csv").read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
