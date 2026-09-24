"""A bounded, foreground, one-shot booking launcher.

It does not install a background task.  Keep this process and the WeChat
debugger running until it finishes.  It is not an unattended production
booking service: at the target time, booking.py performs its own fresh checks.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import time

from tennis import InputError, TZ
from booking_config import load_config, config_sources


ROOT = Path(__file__).resolve().parent
MAX_LATENESS_SECONDS = 30
MAX_CLOCK_DRIFT_SECONDS = 2


def parse_target(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+08:00", value):
        raise InputError("--at 必须是明确的 YYYY-MM-DDTHH:MM:SS+08:00 时间。")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise InputError("--at 不是有效日期时间。") from None
    if result.utcoffset() != timedelta(hours=8):
        raise InputError("--at 必须使用 +08:00。")
    return result.astimezone(TZ)


def validate_config(path, target):
    config = load_config(path)
    booking_day = date.fromisoformat(config['date'])
    if booking_day not in {target.date(), target.date() + timedelta(days=1)}:
        raise InputError('配置预约日期必须是定时目标当天或次日。')
    return config


def config_fingerprints(path):
    return {str(source.resolve()): hashlib.sha256(source.read_bytes()).hexdigest()
            for source in config_sources(path)}

def run_submit(config_path):
    """Run the single existing submit command once, at the target only."""
    completed = subprocess.run(
        [sys.executable, str(ROOT / "booking.py"), "submit", "--config", str(config_path)],
        check=False,
    )
    return completed.returncode


def wait_and_run(target, config_path, *, submit=False, clock=None, monotonic=None,
                 sleep=None, runner=None):
    """Wait once; injected dependencies make timing logic testable without waiting."""
    clock = clock or (lambda: datetime.now(TZ))
    monotonic = monotonic or time.monotonic
    sleep = sleep or time.sleep
    runner = runner or run_submit
    fingerprints = config_fingerprints(config_path)
    config = validate_config(config_path, target)
    if config_fingerprints(config_path) != fingerprints:
        raise InputError('配置在读取期间变化，停止执行。')
    now = clock().astimezone(TZ)
    if now > target + timedelta(seconds=MAX_LATENESS_SECONDS):
        raise InputError("已超过定时点30秒，拒绝执行。")
    previous_wall, previous_monotonic = now, monotonic()
    while now < target:
        sleep(min(1.0, max(0.0, (target - now).total_seconds())))
        now = clock().astimezone(TZ)
        current_monotonic = monotonic()
        wall_elapsed = (now - previous_wall).total_seconds()
        monotonic_elapsed = current_monotonic - previous_monotonic
        if abs(wall_elapsed - monotonic_elapsed) > MAX_CLOCK_DRIFT_SECONDS:
            raise InputError("检测到系统时钟跳变或睡眠，拒绝执行。")
        if now > target + timedelta(seconds=MAX_LATENESS_SECONDS):
            raise InputError("已超过定时点30秒，拒绝执行。")
        previous_wall, previous_monotonic = now, current_monotonic
    if config_fingerprints(config_path) != fingerprints:
        raise InputError('等待期间配置或账号文件已修改，请重新启动定时任务。')
    if not submit:
        return {"state": "dry_run", "submit_attempts": 0, "date": config["date"]}
    returncode = runner(config_path)
    return {"state": "submit_started" if returncode == 0 else "submit_failed",
            "submit_attempts": 1, "runner_returncode": returncode, "date": config["date"]}


def main():
    parser = argparse.ArgumentParser(
        description="一次性前台定时器；默认演练。需保持微信调试器在线，非无人值守正式服务。"
    )
    parser.add_argument("--at", required=True, help="明确的 +08:00 时间")
    parser.add_argument("--config", required=True, help="预约配置JSON")
    parser.add_argument("--submit", action="store_true", help="到点后仅调用一次 booking.py submit")
    args = parser.parse_args()
    try:
        result = wait_and_run(parse_target(args.at), args.config, submit=args.submit)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["state"] in {"dry_run", "submit_started"} else 1
    except (InputError, OSError, ValueError, TypeError):
        print(json.dumps({"state": "stopped", "message": "定时条件不满足，未自动重试。"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
