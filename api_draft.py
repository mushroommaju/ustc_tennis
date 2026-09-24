"""Build an unsigned booking body in memory; never send or copy old signatures."""
from copy import deepcopy

from tennis import InputError, parse_clock, preview, snapshot


def build_unsigned(entry, data, config, companion_ids):
    """Use friends returned by this venue response; IDs must be explicitly chosen.

    The result still needs fresh authentication, noise and a verified signature.
    Not a submission-ready request. Callers must not log the returned personal data.
    """
    if (not isinstance(companion_ids, list) or not companion_ids or
            any(type(i) is not int for i in companion_ids) or
            len(set(companion_ids)) != len(companion_ids)):
        raise InputError("请明确指定不重复的同伴整数ID。")
    if config.get("companion_count") != len(companion_ids):
        raise InputError("同伴人数与明确选择的ID数量不一致。")
    snap = snapshot(entry, data)
    plan = preview(snap, config)
    court_id = plan["preferred_snapshot_court"]
    if court_id is None:
        raise InputError("当前数据中没有满足条件的场地，不生成提交草案。")
    if data.get("venue_info", {}).get("id") != 2:
        raise InputError("不是已验证的网球场类型。")
    friends = data.get("partner_list")
    if not isinstance(friends, list):
        raise InputError("未取得已添加好友列表，需要新查场响应。")
    picked = []
    for friend_id in companion_ids:
        matches = [p for p in friends if isinstance(p, dict) and p.get("id") == friend_id]
        if len(matches) != 1:
            raise InputError("同伴未在当前已添加好友列表中唯一匹配，需核对新抓包结构。")
        person = matches[0]
        fields = ("id", "avatar", "nickname", "pinyin", "salary_no")
        if not all(k in person for k in fields):
            raise InputError("已添加好友结构与已验证的提交结构不同，需补抓包核对。")
        picked.append({**{k: deepcopy(person[k]) for k in fields}, "selected": True})
    court = next(c for c in snap["courts"] if c["id"] == court_id)
    day = next(d for d in snap["days"] if d["date"] == config["date"])
    start = parse_clock(config["start"])
    end = 1440 if config["end"] == "24:00" else parse_clock(config["end"])
    share = config.get("share_open")
    if type(share) is not bool:
        raise InputError("请在配置中明确设置 share_open 为 true 或 false。")
    return {"day": day["day"], "kind_id": 2, "share_open": int(share),
            "selected_block": [{"hour": t // 60, "min": t % 60,
                                "venue_id": court_id, "venue": deepcopy(court)}
                               for t in range(start, end, 15)],
            "selected_partner": picked}


def safe_summary(payload):
    """For logs only; never output friend objects or identifiers."""
    return {"mode": "unsigned_draft_only", "submit_enabled": False,
            "day": payload["day"], "kind_id": payload["kind_id"],
            "blocks": [{k: b[k] for k in ("hour", "min", "venue_id")}
                       for b in payload["selected_block"]],
            "companion_count": len(payload["selected_partner"]),
            "missing": ["fresh_session", "verified_signing", "verified_noise",
                        "fresh_availability", "account_and_companion_eligibility"]}
