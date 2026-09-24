import copy
import hashlib
import unittest
from urllib.parse import parse_qsl, urlsplit

from test_tennis import entry
import tennis
import signing


class SigningTests(unittest.TestCase):
    def test_sort_skip_trim_json_and_bool(self):
        p = {"z": " hi ", "a": [{"x": 1, "y": True}], "b": False,
             "skip": None, "empty": " ", "sign": "OLD"}
        canonical = 'a=[{"x":1,"y":true}]&b=false&z=hi' + 'test'
        self.assertEqual(signing.sign(p, "test"), hashlib.md5(canonical.encode()).hexdigest())

    def test_fresh_timestamp_signature_keeps_session_and_original(self):
        e = entry('/api/venue/tennis?open_id=PERSON&version=113&timestamp=1&sign=OLD')
        saved = copy.deepcopy(e)
        out = signing.fresh_query(e, 'test', 1000)
        p = dict(parse_qsl(urlsplit(out['request']['url']).query))
        self.assertEqual(p['timestamp'], '1000')
        self.assertEqual(p['sign'], signing.sign(p, 'test'))
        self.assertEqual(e, saved)
        self.assertEqual(e['request']['headers'], out['request']['headers'])

    def test_reject_post_duplicates_cross_domain_and_unknown_numbers(self):
        examples = [entry(method='POST'), entry('/api/venue/tennis?x=1&x=2')]
        cross = entry(); cross['request']['url'] = 'https://example.invalid/api/venue/tennis'
        examples.append(cross)
        for e in examples:
            with self.assertRaises(tennis.InputError):
                signing.fresh_query(e, 'test')
        with self.assertRaises(tennis.InputError):
            signing.sign({'x': 1.5}, 'test')

    def test_latest_query_session_uses_latest_successful_session(self):
        old = entry('/api/venue/tennis?open_id=PERSON&version=113&timestamp=1&sign=OLD')
        latest = entry('/api/venue/tennis?open_id=PERSON&version=113&timestamp=2&sign=NEW')
        self.assertIs(signing.latest_query_session([old, latest]), latest)

    def test_latest_query_session_never_falls_back_after_newer_failure(self):
        old = entry('/api/venue/tennis?open_id=PERSON&version=113&timestamp=1&sign=OLD')
        failed = entry('/api/venue/tennis?open_id=PERSON&version=113&timestamp=2&sign=NEW')
        failed['response']['status'] = 401
        with self.assertRaisesRegex(tennis.InputError, '最新网球查场未成功'):
            signing.latest_query_session([old, failed])

    def test_latest_query_session_rejects_missing_or_mixed_identity(self):
        missing = entry('/api/venue/tennis?open_id=PERSON&version=113&timestamp=1&sign=OLD')
        missing['request']['headers'] = []
        with self.assertRaisesRegex(tennis.InputError, '有效登录会话'):
            signing.latest_query_session([missing])

        one = entry('/api/venue/tennis?open_id=PERSON&version=113&timestamp=1&sign=OLD')
        other = entry('/api/venue/tennis?open_id=OTHER&version=113&timestamp=2&sign=NEW')
        with self.assertRaisesRegex(tennis.InputError, '多个网球查场账号'):
            signing.latest_query_session([one, other])


if __name__ == '__main__':
    unittest.main()
