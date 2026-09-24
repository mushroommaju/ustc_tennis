"""Strict local booking configuration loading; never logs account or friend values."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re

from tennis import InputError, parse_clock


DISPLAY_COURTS = {number: number for number in range(1, 7)} | {7: 121, 8: 122, 9: 154}
_USER_FIELDS = {"date", "start", "end", "courts", "share_open"}
_PROFILE_FIELDS = {"expected_open_id", "companion_ids"}
_LEGACY_FIELDS = {
    "date", "start", "end", "court_priority", "companion_count", "companion_ids",
    "expected_open_id", "share_open",
}


def _read_object(path, message):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=_no_duplicate_keys)
    except (OSError, ValueError, TypeError):
        raise InputError(message) from None
    if not isinstance(value, dict):
        raise InputError(message)
    return value


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _validate_common(config):
    value = config.get("date")
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise InputError("配置日期必须是 ISO 日期。")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise InputError("配置日期必须是有效 ISO 日期。") from None
    try:
        start = parse_clock(config["start"])
        end = 1440 if config["end"] == "24:00" else parse_clock(config["end"])
    except (KeyError, TypeError, InputError):
        raise InputError("配置时间必须为 15 分钟粒度。") from None
    duration = end - start
    if duration not in (30, 45, 60, 75, 90):
        raise InputError("预约时长必须为 30 至 90 分钟。")
    if type(config.get("share_open")) is not bool:
        raise InputError("share_open 必须为布尔值。")


def _validate_profile(profile):
    if set(profile) != _PROFILE_FIELDS:
        raise InputError("账号配置字段不完整或包含未知字段。")
    account = profile["expected_open_id"]
    ids = profile["companion_ids"]
    if not isinstance(account, str) or not account.strip():
        raise InputError("账号配置缺少有效账号标识。")
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 3
            or any(type(item) is not int or item <= 0 for item in ids)
            or len(set(ids)) != len(ids)):
        raise InputError("账号配置必须有 1 至 3 个不重复正整数同伴ID。")
    return {"expected_open_id": account, "companion_ids": list(ids)}


def _new_config(config, profile_path, source_path):
    if set(config) != _USER_FIELDS:
        raise InputError("新配置字段不完整或包含未知字段。")
    _validate_common(config)
    courts = config["courts"]
    if (not isinstance(courts, list) or not courts
            or any(type(court) is not int or court not in DISPLAY_COURTS for court in courts)
            or len(set(courts)) != len(courts)):
        raise InputError("courts 必须是不重复的已知显示场号 1 至 9。")
    profile_file = Path(profile_path) if profile_path else source_path.with_name("account.local.json")
    profile = _validate_profile(_read_object(profile_file, "无法读取账号本地配置。"))
    return {
        "date": config["date"], "start": config["start"], "end": config["end"],
        "court_priority": [DISPLAY_COURTS[court] for court in courts],
        "share_open": config["share_open"], "companion_ids": profile["companion_ids"],
        "companion_count": len(profile["companion_ids"]),
        "expected_open_id": profile["expected_open_id"],
    }


def _legacy_config(config):
    if set(config) != _LEGACY_FIELDS:
        raise InputError("旧配置字段不完整或包含未知字段。")
    _validate_common(config)
    courts = config["court_priority"]
    if (not isinstance(courts, list) or not courts
            or any(type(court) is not int or court not in set(DISPLAY_COURTS.values()) for court in courts)
            or len(set(courts)) != len(courts)):
        raise InputError("court_priority 必须是不重复的已知场地ID。")
    profile = _validate_profile({
        "expected_open_id": config["expected_open_id"], "companion_ids": config["companion_ids"],
    })
    if type(config["companion_count"]) is not int or config["companion_count"] != len(profile["companion_ids"]):
        raise InputError("旧配置同伴人数与同伴ID不一致。")
    return {
        "date": config["date"], "start": config["start"], "end": config["end"],
        "court_priority": list(courts), "share_open": config["share_open"],
        "companion_ids": profile["companion_ids"], "companion_count": config["companion_count"],
        "expected_open_id": profile["expected_open_id"],
    }


def load_config(path, profile_path=None):
    """Return the existing normalized config shape without exposing sensitive values."""
    source = _read_object(path, "无法读取预约配置。")
    if "courts" in source:
        return _new_config(source, profile_path, Path(path))
    return _legacy_config(source)


def config_sources(path, profile_path=None):
    """Return every local file that must remain unchanged before submission."""
    source_path = Path(path)
    source = _read_object(source_path, "无法读取预约配置。")
    if "courts" in source:
        return [source_path, Path(profile_path) if profile_path else source_path.with_name("account.local.json")]
    return [source_path]
