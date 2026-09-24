import json
from pathlib import Path
import tempfile
import unittest

import booking_config
import tennis


class BookingConfigTests(unittest.TestCase):
    def write_json(self, directory, name, value):
        path = Path(directory) / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def user_config(self, **overrides):
        value = {"date": "2030-01-02", "start": "23:00", "end": "23:30",
                 "courts": [1, 7, 9], "share_open": True}
        value.update(overrides)
        return value

    def profile(self, **overrides):
        value = {"expected_open_id": "account", "companion_ids": [42, 43]}
        value.update(overrides)
        return value

    def test_new_config_maps_display_courts_and_derives_companion_count(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.write_json(directory, "booking.json", self.user_config())
            self.write_json(directory, "account.local.json", self.profile())
            result = booking_config.load_config(source)
        self.assertEqual(result["court_priority"], [1, 121, 154])
        self.assertEqual(result["companion_count"], 2)
        self.assertEqual(result["date"], "2030-01-02")

    def test_new_config_rejects_unknown_fields_and_invalid_values(self):
        invalid = [
            self.user_config(extra=True), self.user_config(date="2030-1-2"),
            self.user_config(start="23:05"), self.user_config(end="23:15"),
            self.user_config(courts=[1, 1]), self.user_config(courts=[True]),
            self.user_config(courts=[10]), self.user_config(share_open=1),
        ]
        with tempfile.TemporaryDirectory() as directory:
            profile = self.write_json(directory, "account.local.json", self.profile())
            for index, value in enumerate(invalid):
                with self.subTest(index=index):
                    source = self.write_json(directory, "booking.json", value)
                    with self.assertRaises(tennis.InputError):
                        booking_config.load_config(source, profile)

    def test_profile_is_strict_and_legacy_config_remains_supported(self):
        legacy = {"date": "2030-01-02", "start": "23:00", "end": "23:30",
                  "court_priority": [1, 121], "companion_count": 1, "companion_ids": [42],
                  "expected_open_id": "account", "share_open": False}
        with tempfile.TemporaryDirectory() as directory:
            legacy_path = self.write_json(directory, "legacy.json", legacy)
            invalid_profile = self.write_json(directory, "profile.json", {"expected_open_id": "", "companion_ids": [42]})
            self.assertEqual(booking_config.load_config(legacy_path, invalid_profile)["court_priority"], [1, 121])
            source = self.write_json(directory, "booking.json", self.user_config())
            with self.assertRaises(tennis.InputError):
                booking_config.load_config(source, invalid_profile)
            legacy["companion_count"] = True
            bad_legacy = self.write_json(directory, "bad.json", legacy)
            with self.assertRaises(tennis.InputError):
                booking_config.load_config(bad_legacy)

    def test_duplicate_json_keys_are_rejected_and_sources_include_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "booking.json"
            source.write_text('{"date":"2030-01-02","date":"2030-01-03"}', encoding="utf-8")
            with self.assertRaises(tennis.InputError):
                booking_config.load_config(source)
            source = self.write_json(directory, "booking.json", self.user_config())
            profile = self.write_json(directory, "account.local.json", self.profile())
            self.assertEqual(booking_config.config_sources(source), [source, profile])


if __name__ == "__main__":
    unittest.main()
