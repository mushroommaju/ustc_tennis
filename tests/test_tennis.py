"""Regression tests for the read-only tennis assistant.

All fixtures are synthetic.  They intentionally contain marker strings which
must never escape into analysis or preview output.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import tennis  # noqa: E402


SECRET_TOKEN = "TOKEN_MUST_NOT_APPEAR"
PERSON = "PERSON_MUST_NOT_APPEAR"
URL = "https://sport.ustc.edu.cn/api/venue/tennis"


def cell(flag="normal", **overrides):
    value = {
        "flag": flag,
        "passed": False,
        "friend": False,
        "ori_order": False,
        "my_order": False,
        "invite_me": False,
        "is_mine": False,
        "seat": "",
        "percent": 0,
    }
    value.update(overrides)
    return value


def venue_data(block=None, *, closed=False):
    if block is None:
        block = {
            "10": {
                "0": {"1": cell(), "2": cell("checked", percent=100)},
                "15": {"1": cell(), "2": cell()},
                "30": {"1": cell("passed", passed=True), "2": cell()},
                "45": {"1": cell(), "2": cell()},
            }
        }
    return {
        "venue_info": {
            "min_block": 2,
            "max_block": 6,
            "need_partner": 1,
            "max_partner": 3,
            "must_partner": True,
        },
        "venue_list": [
            {"id": 1, "sn": "1号", "school_part": 10},
            {"id": 2, "sn": "2号", "school_part": 10},
        ],
        "time_block_data": [
            {"title": "今天", "desc": "x", "day": 1789488000,
             "passed": 0, "closed": closed, "block": block},
            {"title": "明天", "desc": "y", "day": 1789574400,
             "passed": 0, "closed": False, "block": None},
        ],
    }


def entry(path="/api/venue/tennis", *, method="GET", data=None, body=None,
          content_encoding=None):
    response_text = json.dumps({"code": 0, "data": venue_data() if data is None else data})
    content = {"text": response_text}
    if content_encoding:
        content = {"text": base64.b64encode(response_text.encode()).decode(),
                   "encoding": content_encoding}
    request = {
        "method": method,
        "url": "https://sport.ustc.edu.cn" + path,
        "headers": [{"name": "Authorization", "value": "Bearer " + SECRET_TOKEN}],
    }
    if body is not None:
        request["postData"] = {"text": body}
    return {"startedDateTime": "2026-09-16T09:00:00+08:00", "request": request,
            "response": {"status": 200, "content": content}}


class HarAndSnapshotTests(unittest.TestCase):
    def test_base64_response_and_base64_post_parameters_are_decoded(self):
        payload = {"all_params": {"day": 1789488000, "selected_partner": [
            {"id": 7, "nickname": PERSON, "selected": True}], "sign": "abc"}}
        body = json.dumps(base64.b64encode(json.dumps(payload).encode()).decode())
        post = entry("/api/venue/order", method="POST", body=body)
        self.assertEqual(tennis.params(post)["day"], 1789488000)
        self.assertEqual(tennis.params(post)["selected_partner"][0]["selected"], True)

        decoded = tennis.response_json(entry(content_encoding="base64"))
        self.assertEqual(decoded["code"], 0)
        self.assertIn("venue_info", decoded["data"])

    def test_latest_venue_uses_the_latest_successful_response(self):
        old = entry(data={"venue_info": {}, "venue_list": [], "time_block_data": []})
        latest = entry(data=venue_data())
        chosen, data = tennis.latest_venue([old, latest])
        self.assertIs(chosen, latest)
        self.assertEqual(data["venue_list"][0]["id"], 1)

    def test_analysis_and_snapshot_are_redacted(self):
        friend = entry("/api/friend/search?keyword=" + PERSON)
        friend["response"]["content"]["text"] = json.dumps(
            {"code": 0, "data": {"list": [{"nickname": PERSON, "id": 7}]}})
        result = tennis.analyze([friend])
        snap = tennis.snapshot(entry(), venue_data())
        rendered = json.dumps({"analysis": result, "snapshot": snap})
        self.assertNotIn(SECRET_TOKEN, rendered)
        self.assertNotIn(PERSON, rendered)
        self.assertEqual(result["business_requests"][0]["parameter_names"], ["keyword"])


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.snap = tennis.snapshot(entry(), venue_data())
        self.config = {"date": self.snap["days"][0]["date"], "start": "10:00",
                       "end": "10:30", "companion_count": 1,
                       "court_priority": [2, 1]}

    def test_preview_picks_first_safe_court_in_priority_order(self):
        outcome = tennis.preview(self.snap, self.config)
        self.assertFalse(outcome["candidates"][0]["snapshot_candidate"])
        self.assertTrue(outcome["candidates"][1]["snapshot_candidate"])
        self.assertEqual(outcome["preferred_snapshot_court"], 1)
        self.assertFalse(outcome["submit_enabled"])

    def test_preview_rejects_invalid_duration_unknown_court_and_people_count(self):
        for changed in (
            {"end": "10:15"},
            {"court_priority": [99]},
            {"companion_count": 0},
            {"companion_count": 4},
        ):
            config = dict(self.config, **changed)
            with self.subTest(changed=changed), self.assertRaises(tennis.InputError):
                tennis.preview(self.snap, config)

    def test_tomorrow_without_block_data_has_no_candidates(self):
        config = dict(self.config, date=self.snap["days"][1]["date"])
        outcome = tennis.preview(self.snap, config)
        self.assertFalse(outcome["day_has_block_data"])
        self.assertEqual(outcome["preferred_snapshot_court"], None)
        self.assertTrue(all(not c["snapshot_candidate"] for c in outcome["candidates"]))


class ProbeTests(unittest.TestCase):
    def test_probe_refuses_post_and_cross_domain_before_opening_network(self):
        opener = unittest.mock.Mock()
        with patch.object(tennis.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(tennis.InputError):
                tennis.probe(entry(method="POST"))
            cross = entry()
            cross["request"]["url"] = "https://example.invalid/api/venue/tennis"
            with self.assertRaises(tennis.InputError):
                tennis.probe(cross)
        opener.open.assert_not_called()

    def test_probe_blocks_redirect_and_does_not_follow_or_expose_auth(self):
        class RedirectOpener:
            def __init__(self):
                self.requests = []

            def open(self, request, timeout):
                self.requests.append(request)
                raise urllib.error.HTTPError(request.full_url, 302, "Found", {}, None)

        opener = RedirectOpener()
        captured_handlers = []

        def build_opener(*handlers):
            captured_handlers.extend(handlers)
            return opener

        with patch.object(tennis.urllib.request, "build_opener", side_effect=build_opener):
            outcome = tennis.probe(entry())
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["http_status"], 302)
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(opener.requests[0].full_url, URL)
        self.assertIn("Bearer " + SECRET_TOKEN, opener.requests[0].headers.values())
        self.assertTrue(any(isinstance(h, tennis.NoRedirect) for h in captured_handlers))
        self.assertIsNone(tennis.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid/"))
        self.assertNotIn(SECRET_TOKEN, json.dumps(outcome))


if __name__ == "__main__":
    unittest.main()
