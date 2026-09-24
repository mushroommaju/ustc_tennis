import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

import booking_protocol
import signing
import tennis


DAY = int(datetime(2026, 9, 18, tzinfo=tennis.TZ).timestamp())
SESSION = {"open_id": "PERSON", "version": "113", "token": "TOKEN",
           "deviceFingerprint": "DEVICE", "environment": "ENV"}


def payload():
    venue = {"id": 2, "sn": "2号", "school_part": 10}
    partner = {"id": 42, "avatar": "avatar", "nickname": "friend", "pinyin": "friend",
               "salary_no": "number", "selected": True}
    return {"day": DAY, "kind_id": 2, "share_open": True,
            "selected_block": [
                {"hour": 23, "min": 0, "venue_id": 2, "venue": venue},
                {"hour": 23, "min": 15, "venue_id": 2, "venue": venue},
            ], "selected_partner": [partner]}


def payload_at(hour, minute):
    result = payload()
    result["selected_block"] = [
        {**block, "hour": hour + (minute + offset) // 60, "min": (minute + offset) % 60}
        for offset, block in ((0, result["selected_block"][0]), (15, result["selected_block"][1]))
    ]
    return result


class BookingProtocolTests(unittest.TestCase):
    def test_prepare_booking_matches_json_string_base64_wire_shape(self):
        draft = payload()
        result = booking_protocol.prepare_booking(draft, SESSION, "NOISE", "suffix", 1000)
        outer = json.loads(result["body"])
        params = json.loads(base64.b64decode(outer))["all_params"]
        self.assertEqual(params["noise"], "NOISE")
        self.assertEqual(params["open_id"], "PERSON")
        self.assertEqual(params["timestamp"], 1000)
        self.assertEqual(params["sign"], signing.sign(params, "suffix"))
        self.assertEqual(result["headers"]["Authorization"], "Bearer TOKEN")
        self.assertEqual(result["headers"]["DeviceFingerprint"], "DEVICE")
        self.assertEqual(draft, payload())

    def test_prepare_booking_preserves_the_verified_share_open_value(self):
        draft = payload()
        draft["share_open"] = 1
        result = booking_protocol.prepare_booking(draft, SESSION, "NOISE", "suffix", 1000)
        params = json.loads(base64.b64decode(json.loads(result["body"])))["all_params"]
        self.assertEqual(params["share_open"], 1)

    def test_prepare_booking_rejects_reused_fields_and_bad_business_shape(self):
        for mutate in (
            lambda p: p.update({"noise": "old"}),
            lambda p: p["selected_block"].reverse(),
            lambda p: p.update({"day": DAY + 60}),
            lambda p: p.update({"selected_partner": []}),
        ):
            with self.subTest(mutate=mutate):
                draft = payload()
                mutate(draft)
                with self.assertRaises(tennis.InputError):
                    booking_protocol.prepare_booking(draft, SESSION, "NOISE", "suffix", 1000)

    def test_ledger_prevents_concurrent_and_restarted_duplicate_reservation(self):
        key = booking_protocol.booking_intent_hash(payload(), SESSION)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "ledger.sqlite3"
            ledger = booking_protocol.BookingLedger(database)
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: ledger.reserve(key, 1000), range(4)))
            self.assertEqual(results.count(True), 1)
            ledger.mark(key, "known_failed")
            restarted = booking_protocol.BookingLedger(database)
            self.assertFalse(restarted.reserve(key, 2000))
            self.assertEqual(restarted.state(key), "known_failed")

    def test_slot_claims_block_overlap_after_unknown_but_allow_other_account_or_slot(self):
        first = payload_at(23, 0)
        overlap = payload_at(23, 15)
        earlier = payload_at(22, 30)
        other_session = {**SESSION, "open_id": "OTHER", "token": "OTHER_TOKEN"}
        with tempfile.TemporaryDirectory() as directory:
            ledger = booking_protocol.BookingLedger(Path(directory) / "ledger.sqlite3")
            first_key = booking_protocol.booking_intent_hash(first, SESSION)
            self.assertTrue(ledger.reserve(first_key, 1000, booking_protocol.booking_slot_claims(first, SESSION)))
            ledger.mark(first_key, "unknown")
            self.assertFalse(ledger.reserve(
                booking_protocol.booking_intent_hash(overlap, SESSION), 2000,
                booking_protocol.booking_slot_claims(overlap, SESSION),
            ))
            self.assertTrue(ledger.reserve(
                booking_protocol.booking_intent_hash(earlier, SESSION), 3000,
                booking_protocol.booking_slot_claims(earlier, SESSION),
            ))
            self.assertTrue(ledger.reserve(
                booking_protocol.booking_intent_hash(overlap, other_session), 4000,
                booking_protocol.booking_slot_claims(overlap, other_session),
            ))


if __name__ == "__main__":
    unittest.main()
