from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

import scheduler
import tennis


TARGET = datetime(2026, 9, 17, 17, 0, 0, tzinfo=tennis.TZ)


class FakeTime:
    def __init__(self, moments, monotonic_values):
        self.moments = iter(moments)
        self.monotonic_values = iter(monotonic_values)
        self.sleeps = []

    def clock(self):
        return next(self.moments)

    def monotonic(self):
        return next(self.monotonic_values)

    def sleep(self, seconds):
        self.sleeps.append(seconds)


class SchedulerTests(unittest.TestCase):
    def config_file(self, directory, **overrides):
        config = {"date": "2026-09-18", "start": "23:00", "end": "23:30",
                  "court_priority": [2], "companion_count": 1, "companion_ids": [42], "expected_open_id":"test-account", "share_open":True}
        config.update(overrides)
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def test_explicit_target_and_config_validation(self):
        self.assertEqual(scheduler.parse_target("2026-09-17T17:00:00+08:00"), TARGET)
        with self.assertRaises(tennis.InputError):
            scheduler.parse_target("2026-09-17T17:00:00Z")
        with tempfile.TemporaryDirectory() as directory:
            path = self.config_file(directory, companion_ids=[])
            with self.assertRaises(tennis.InputError):
                scheduler.validate_config(path, TARGET)

    def test_dry_run_waits_in_one_second_steps_and_never_calls_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.config_file(directory)
            fake = FakeTime([TARGET - timedelta(seconds=2), TARGET - timedelta(seconds=1), TARGET], [0, 1, 2])
            calls = []
            result = scheduler.wait_and_run(TARGET, path, clock=fake.clock, monotonic=fake.monotonic,
                                            sleep=fake.sleep, runner=lambda _: calls.append(True) or 0)
        self.assertEqual(result["state"], "dry_run")
        self.assertEqual(calls, [])
        self.assertEqual(fake.sleeps, [1.0, 1.0])

    def test_submit_calls_runner_once_and_late_or_clock_jump_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.config_file(directory)
            calls = []
            at_target = FakeTime([TARGET], [0])
            result = scheduler.wait_and_run(TARGET, path, submit=True, clock=at_target.clock,
                                            monotonic=at_target.monotonic, sleep=at_target.sleep,
                                            runner=lambda value: calls.append(value) or 0)
            self.assertEqual(result["submit_attempts"], 1)
            self.assertEqual(calls, [path])

            late = FakeTime([TARGET + timedelta(seconds=31)], [0])
            with self.assertRaises(tennis.InputError):
                scheduler.wait_and_run(TARGET, path, submit=True, clock=late.clock,
                                       monotonic=late.monotonic, sleep=late.sleep, runner=lambda _: 0)

            jumped = FakeTime([TARGET - timedelta(seconds=1), TARGET + timedelta(seconds=10)], [0, 1])
            with self.assertRaises(tennis.InputError):
                scheduler.wait_and_run(TARGET, path, submit=True, clock=jumped.clock,
                                       monotonic=jumped.monotonic, sleep=jumped.sleep, runner=lambda _: 0)


    def test_config_change_while_waiting_never_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            path=self.config_file(directory)
            fake=FakeTime([TARGET-timedelta(seconds=1),TARGET],[0,1])
            calls=[]
            def change_config(seconds):
                data=json.loads(path.read_text())
                data['start']='22:30'
                path.write_text(json.dumps(data))
            with self.assertRaises(tennis.InputError):
                scheduler.wait_and_run(TARGET,path,submit=True,clock=fake.clock,monotonic=fake.monotonic,sleep=change_config,runner=lambda _:calls.append(True))
            self.assertEqual(calls,[])

if __name__ == "__main__":
    unittest.main()
