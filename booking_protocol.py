"""Pure booking-body encoding and durable local duplicate prevention.

This module intentionally performs no HTTP, runtime bridge, or submission.
Callers must reserve an intent before any future send operation.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

import signing
from tennis import InputError, TZ


ORDER_PATH = "/api/venue/order"
_OUTCOMES = {"pending", "unknown", "success", "known_failed"}


def _integer(value, message):
    if type(value) is not int:
        raise InputError(message)
    return value


def _string(value, message):
    if not isinstance(value, str) or not value:
        raise InputError(message)
    return value


def _validate_day(day):
    _integer(day, "预约日期必须是整数秒时间戳。")
    try:
        local = datetime.fromtimestamp(day, TZ)
    except (OverflowError, OSError, ValueError):
        raise InputError("预约日期超出可验证范围。") from None
    if (local.hour, local.minute, local.second, local.microsecond) != (0, 0, 0, 0):
        raise InputError("预约日期必须是上海时区当天零点。")


def _validated_payload(payload):
    if not isinstance(payload, dict):
        raise InputError("预约草案必须是对象。")
    expected = {"day", "kind_id", "share_open", "selected_block", "selected_partner"}
    prohibited = {"sign", "noise", "open_id", "version", "timestamp"}
    keys = set(payload)
    if prohibited & keys or keys != expected:
        raise InputError("预约草案字段与已验证的新预约结构不一致。")

    result = deepcopy(payload)
    _validate_day(result["day"])
    if result["kind_id"] != 2 or type(result["kind_id"]) is not int:
        raise InputError("仅允许已验证的网球场类型。")
    if not (type(result["share_open"]) is bool or
            (type(result["share_open"]) is int and result["share_open"] in (0, 1))):
        raise InputError("share_open 必须为布尔值或已验证的 0/1。")

    blocks = result["selected_block"]
    if not isinstance(blocks, list) or not 2 <= len(blocks) <= 6:
        raise InputError("必须连续选择 2 至 6 个时间块。")
    times, court_ids = [], set()
    for block in blocks:
        if not isinstance(block, dict) or set(block) != {"hour", "min", "venue_id", "venue"}:
            raise InputError("时间块字段与已验证结构不一致。")
        hour, minute, court_id = block["hour"], block["min"], block["venue_id"]
        if (type(hour) is not int or type(minute) is not int or type(court_id) is not int
                or not 0 <= hour <= 23 or minute not in (0, 15, 30, 45) or court_id <= 0):
            raise InputError("时间块格式无效。")
        venue = block["venue"]
        if (not isinstance(venue, dict) or set(venue) != {"id", "sn", "school_part"}
                or venue["id"] != court_id or type(venue["id"]) is not int
                or not isinstance(venue["sn"], str) or type(venue["school_part"]) is not int):
            raise InputError("场地字段与已验证结构不一致。")
        times.append(hour * 60 + minute)
        court_ids.add(court_id)
    if len(court_ids) != 1 or times != sorted(times) or any(
            later - earlier != 15 for earlier, later in zip(times, times[1:])):
        raise InputError("时间块必须为同一场地连续且从早到晚。")

    partners = result["selected_partner"]
    partner_keys = {"id", "avatar", "nickname", "pinyin", "salary_no", "selected"}
    if not isinstance(partners, list) or not 1 <= len(partners) <= 3:
        raise InputError("必须选择 1 至 3 名同伴。")
    partner_ids = set()
    for partner in partners:
        if not isinstance(partner, dict) or set(partner) != partner_keys:
            raise InputError("同伴字段与已验证结构不一致。")
        if (type(partner["id"]) is not int or partner["id"] <= 0
                or type(partner["selected"]) is not bool or not partner["selected"]
                or any(not isinstance(partner[field], str)
                       for field in ("avatar", "nickname", "pinyin", "salary_no"))):
            raise InputError("同伴数据无效。")
        partner_ids.add(partner["id"])
    if len(partner_ids) != len(partners):
        raise InputError("同伴不能重复。")
    return result


def _validated_session(session):
    if not isinstance(session, dict):
        raise InputError("会话必须是对象。")
    result = {
        "open_id": _string(session.get("open_id"), "会话缺少账号标识。"),
        "version": _string(session.get("version"), "会话缺少小程序版本。"),
        "token": _string(session.get("token"), "会话缺少登录令牌。"),
    }
    for key in ("deviceFingerprint", "environment"):
        value = session.get(key, "")
        if not isinstance(value, str):
            raise InputError("会话设备字段无效。")
        result[key] = value
    return result


def booking_intent_hash(payload, session):
    """Hash account/day/time only: changing courts or friends cannot bypass a prior attempt."""
    draft = _validated_payload(payload)
    account = _validated_session(session)["open_id"]
    blocks = draft["selected_block"]
    intent = {
        "user": account,
        "day": draft["day"],
        
        "blocks": [block["hour"] * 60 + block["min"] for block in blocks],
        
    }
    canonical = json.dumps(intent, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def booking_slot_claims(payload, session):
    """Return one durable claim per desired 15-minute slot for this account."""
    draft = _validated_payload(payload)
    account = _validated_session(session)["open_id"]
    claims = []
    for block in draft["selected_block"]:
        slot = {
            "user": account,
            "day": draft["day"],
            "minute": block["hour"] * 60 + block["min"],
        }
        canonical = json.dumps(slot, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        claims.append(hashlib.sha256(canonical.encode("utf-8")).hexdigest())
    return claims


def prepare_booking(payload, session, noise, suffix, now_ms):
    """Create the captured JSON-string/Base64 POST body without sending it."""
    draft = _validated_payload(payload)
    session_data = _validated_session(session)
    _string(noise, "noise 必须是新的非空字符串。")
    _integer(now_ms, "时间戳必须是整数毫秒。")
    if now_ms <= 0:
        raise InputError("时间戳必须为正整数毫秒。")

    parameters = {
        **draft,
        "noise": noise,
        "open_id": session_data["open_id"],
        "version": session_data["version"],
        "timestamp": now_ms,
    }
    parameters["sign"] = signing.sign(parameters, suffix)
    inner = json.dumps({"all_params": parameters}, ensure_ascii=False, separators=(",", ":"))
    encoded = base64.b64encode(inner.encode("utf-8")).decode("ascii")
    body = json.dumps(encoded, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {
        "body": body,
        "headers": {
            "Authorization": "Bearer " + session_data["token"],
            "Platform": "MiniProgram",
            "DeviceFingerprint": session_data["deviceFingerprint"],
            "Wx-Env": session_data["environment"],
            "Content-Type": "application/json",
        },
    }


class BookingLedger:
    """SQLite uniqueness gate. Existing intents are never automatically retried."""

    def __init__(self, path):
        self.path = Path(path)
        with self._connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS booking_intents ("
                "intent_hash TEXT PRIMARY KEY, state TEXT NOT NULL, created_at_ms INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS slot_claims ("
                "claim_hash TEXT PRIMARY KEY, intent_hash TEXT NOT NULL)"
            )

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def reserve(self, intent_hash, now_ms, claims=None):
        if not isinstance(intent_hash, str) or len(intent_hash) != 64:
            raise InputError("预约意图哈希无效。")
        _integer(now_ms, "记录时间必须是整数毫秒。")
        if now_ms <= 0:
            raise InputError("记录时间必须为正整数毫秒。")
        if claims is None:
            claims = []
        if (not isinstance(claims, list) or len(set(claims)) != len(claims)
                or any(not isinstance(claim, str) or len(claim) != 64 for claim in claims)):
            raise InputError("预约时间占位无效。")
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO booking_intents(intent_hash, state, created_at_ms) VALUES (?, ?, ?)",
                    (intent_hash, "pending", now_ms),
                )
                connection.executemany(
                    "INSERT INTO slot_claims(claim_hash, intent_hash) VALUES (?, ?)",
                    [(claim, intent_hash) for claim in claims],
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def mark(self, intent_hash, state):
        if state not in _OUTCOMES:
            raise InputError("预约状态无效。")
        with self._connection() as connection:
            updated = connection.execute(
                "UPDATE booking_intents SET state = ? WHERE intent_hash = ?", (state, intent_hash)
            ).rowcount
        if updated != 1:
            raise InputError("预约意图不存在。")

    def state(self, intent_hash):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT state FROM booking_intents WHERE intent_hash = ?", (intent_hash,)
            ).fetchone()
        return None if row is None else row[0]
