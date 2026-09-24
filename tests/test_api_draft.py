import json
import unittest

from test_tennis import entry, venue_data, PERSON
import api_draft
import tennis


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.data = venue_data()
        self.data["venue_info"]["id"] = 2
        self.data["partner_list"] = [{"id": 42, "avatar": "private-avatar",
                                     "nickname": PERSON, "pinyin": "private",
                                     "salary_no": "private-number", "extra": "discard"}]
        self.config = {"date": "2026-09-16", "start": "10:00", "end": "10:30",
                       "court_priority": [1], "companion_count": 1, "share_open": False}

    def test_chronological_blocks_and_no_old_signature(self):
        p = api_draft.build_unsigned(entry(), self.data, self.config, [42])
        self.assertEqual([(b["hour"], b["min"]) for b in p["selected_block"]], [(10, 0), (10, 15)])
        self.assertEqual(p["day"], self.data["time_block_data"][0]["day"])
        self.assertNotIn("sign", p)
        self.assertNotIn("noise", p)
        self.assertNotIn("extra", p["selected_partner"][0])
        self.assertTrue(p["selected_partner"][0]["selected"])
        self.assertNotIn("selected", self.data["partner_list"][0])
        self.assertNotIn(PERSON, json.dumps(api_draft.safe_summary(p)))

    def test_missing_friend_or_unknown_schema_fails_closed(self):
        for friends in ([], [{"id": 42}], self.data["partner_list"] * 2):
            with self.subTest(friends_count=len(friends)):
                self.data["partner_list"] = friends
                with self.assertRaises(tennis.InputError):
                    api_draft.build_unsigned(entry(), self.data, self.config, [42])

    def test_unavailable_blocks_and_ambiguous_preferences_rejected(self):
        with self.assertRaises(tennis.InputError):
            api_draft.build_unsigned(entry(), self.data, self.config, [42, 42])
        with self.assertRaises(tennis.InputError):
            api_draft.build_unsigned(entry(), self.data, dict(self.config, share_open=None), [42])
        with self.assertRaises(tennis.InputError):
            api_draft.build_unsigned(entry(), self.data, dict(self.config, court_priority=[2]), [42])


if __name__ == "__main__":
    unittest.main()
