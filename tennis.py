"""USTC tennis capture analysis and read-only assistant. Python standard library only."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

HOST = "sport.ustc.edu.cn"
TZ = timezone(timedelta(hours=8))
VENUE = "/api/venue/tennis"


class InputError(ValueError):
    pass


def read_har(path):
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        entries = obj["log"]["entries"]
        if not isinstance(entries, list):
            raise ValueError()
        return entries
    except (OSError, ValueError, KeyError, TypeError):
        raise InputError("无法读取 HAR，请检查路径及文件格式。") from None


def response_json(entry):
    try:
        content = entry["response"]["content"]
        text = content["text"]
        if content.get("encoding") == "base64":
            text = base64.b64decode(text, validate=True).decode("utf-8")
        return json.loads(text)
    except (KeyError, ValueError, UnicodeError, TypeError):
        raise InputError("业务响应缺少可解析的 JSON 正文。") from None


def target(entry):
    url = urllib.parse.urlsplit(entry.get("request", {}).get("url", ""))
    return url.hostname == HOST and url.scheme == "https" and url.path.startswith("/api/")


def params(entry):
    request = entry["request"]
    if request["method"] == "GET":
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(request["url"]).query))
    try:
        value = json.loads(request.get("postData", {}).get("text", "{}"))
        if isinstance(value, str):
            value = json.loads(base64.b64decode(value, validate=True))
        value = value.get("all_params", value)
        if isinstance(value, str):
            value = json.loads(value)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError, AttributeError):
        return {}


def header(entry, name):
    return next((h["value"] for h in entry["request"].get("headers", [])
                 if h["name"].lower() == name.lower()), None)


def analyze(entries):
    """Allowlisted metadata only: never emit header, query or person values."""
    rows = []
    venue_samples = []
    joins = []
    for index, entry in enumerate(entries, 1):
        if not target(entry):
            continue
        path = urllib.parse.urlsplit(entry["request"]["url"]).path
        values = params(entry)
        try:
            result = response_json(entry)
        except InputError:
            result = {}
        rows.append({"entry": index, "method": entry["request"]["method"],
                     "path": re.sub(r"BO\d+", "<order_no>", path),
                     "http_status": entry["response"]["status"],
                     "business_code": result.get("code"),
                     "parameter_names": sorted(values)})
        if path == VENUE:
            venue_samples.append(values)
        if path == "/api/login/join" and result.get("code") == 0:
            token = result.get("data", {}).get("token")
            if token:
                joins.append({"entry": index, "used_by_later_request": any(
                    target(e) and header(e, "Authorization") == "Bearer " + token
                    for e in entries[index:])})
    return {"total_entries": len(entries), "business_requests": rows,
            "login_tokens": joins,
            "signature": {"venue_samples": len(venue_samples),
                          "distinct_signatures": len({p.get("sign") for p in venue_samples if p.get("sign")}),
                          "observed_lengths": sorted({len(p["sign"]) for p in venue_samples if isinstance(p.get("sign"), str)}),
                          "algorithm": "MD5 client canonicalization (see signing.py)", "fresh_request_signing_supported": True, "verification": "run signing.py verify with local config"}}


def latest_venue(entries):
    for entry in reversed(entries):
        if (target(entry) and entry["request"]["method"] == "GET"
                and urllib.parse.urlsplit(entry["request"]["url"]).path == VENUE):
            try:
                data = response_json(entry)
                if entry["response"]["status"] == 200 and data.get("code") == 0:
                    return entry, data["data"]
            except (InputError, KeyError, TypeError):
                continue
    raise InputError("HAR 中没有成功且正文完整的网球查场响应。")


def date_of(day):
    return datetime.fromtimestamp(int(day), TZ).date().isoformat()


def snapshot(entry, data):
    info = data["venue_info"]
    days = []
    for day in data["time_block_data"]:
        cells = []
        for hour, minutes in (day.get("block") or {}).items():
            for minute, courts in minutes.items():
                for court_id, cell in courts.items():
                    cells.append({"hour": int(hour), "minute": int(minute),
                                  "court_id": int(court_id),
                                  "state": {k: cell[k] for k in
                                            ("flag", "passed", "friend", "ori_order", "my_order",
                                             "invite_me", "is_mine") if k in cell}})
        days.append({"date": date_of(day["day"]), "day": day["day"],
                     "closed": day.get("closed"),
                     "has_block_data": bool(day.get("block")), "cells": cells})
    return {"source": "har_snapshot", "captured_at": entry.get("startedDateTime"),
            "live": False, "rules": {k: info[k] for k in
                ("min_block", "max_block", "need_partner", "max_partner", "must_partner") if k in info},
            "courts": [{k: c[k] for k in ("id", "sn", "school_part")} for c in data["venue_list"]],
            "days": days}


def parse_clock(value):
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):(?:00|15|30|45)", value):
        raise InputError("时间须为 HH:MM，且分钟为 00/15/30/45。")
    h, m = map(int, value.split(":"))
    return h * 60 + m


def preview(snap, config):
    try:
        date = datetime.strptime(config["date"], "%Y-%m-%d").date()
        start = parse_clock(config["start"])
        end = 1440 if config["end"] == "24:00" else parse_clock(config["end"])
        companions = config["companion_count"]
        courts = config["court_priority"]
    except (KeyError, TypeError, ValueError):
        raise InputError("配置需包含有效 date/start/end/companion_count/court_priority。") from None
    rules = snap["rules"]
    blocks = (end - start) // 15
    if end <= start or not rules["min_block"] <= blocks <= rules["max_block"]:
        raise InputError("时长不符合抓包中的最小/最大预约格数。")
    if (type(companions) is not int or not rules["need_partner"] <= companions <= rules["max_partner"]):
        raise InputError("同伴人数不符合预约规则。")
    if (not isinstance(courts, list) or not courts or
            any(type(c) is not int for c in courts) or len(courts) != len(set(courts))):
        raise InputError("court_priority 必须是不重复的场地整数 ID 列表。")
    known = {c["id"] for c in snap["courts"]}
    if not set(courts) <= known:
        raise InputError("存在未知场地 ID，请使用 snapshot 输出中的 id。")
    day = next((d for d in snap["days"] if d["date"] == date.isoformat()), None)
    if day is None:
        raise InputError("该日期不在抓包快照中，需要新的查场数据。")
    candidates = []
    for court in courts:
        cells = [next((c for c in day["cells"] if c["court_id"] == court and
                       c["hour"] * 60 + c["minute"] == t), None)
                 for t in range(start, end, 15)]
        safe = day.get("closed") is False and bool(day.get("has_block_data")) and all(
            c is not None and c["state"].get("flag") == "normal" and
            c["state"].get("passed") is False and all(c["state"].get(k) is False for k in
                ("friend", "ori_order", "my_order", "invite_me", "is_mine")) for c in cells)
        candidates.append({"court_id": court, "snapshot_candidate": safe,
                           "cells_present": all(c is not None for c in cells),
                           "cell_states": [c["state"] if c else None for c in cells]})
    return {"mode": "preview_only", "date": date.isoformat(), "start": config["start"],
            "end": config["end"], "companion_count": companions,
            "captured_at": snap["captured_at"], "candidates": candidates,
            "preferred_snapshot_court": next((c["court_id"] for c in candidates if c["snapshot_candidate"]), None),
            "day_has_block_data": day.get("has_block_data", False),
            "submit_enabled": False,
            "limitations": ["仅为历史快照，不能证明实时有场或账号剩余额度。",
                            "候选仅依据normal且未过时、无关联订单状态筛选，需实时确认。",
                            "次日17:00开放规则、同伴资格和最终确认需实时校验。"]}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(entry, proxy=None):
    """One GET of the exact captured venue URL, no retries or redirect forwarding."""
    url = urllib.parse.urlsplit(entry["request"]["url"])
    if (not target(entry) or url.path != VENUE or entry["request"]["method"] != "GET"
            or url.port not in (None, 443) or url.username or url.password or url.fragment):
        raise InputError("在线探测仅允许原始 HTTPS 网球查场 GET 请求。")
    if proxy and not re.fullmatch(r"http://127\.0\.0\.1:\d{1,5}", proxy):
        raise InputError("代理仅支持 http://127.0.0.1:端口。")
    allowed = {"authorization", "devicefingerprint", "platform", "wx-env", "user-agent",
               "referer", "accept", "content-type", "cookie", "xweb_xhr"}
    headers = {h["name"]: h["value"] for h in entry["request"].get("headers", [])
               if h["name"].lower() in allowed}
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": proxy} if proxy else {}), NoRedirect())
    try:
        with opener.open(urllib.request.Request(entry["request"]["url"], headers=headers), timeout=15) as res:
            body = res.read(2_000_001)
            if len(body) > 2_000_000:
                raise InputError("响应超出允许大小。")
            parsed = json.loads(body)
            if parsed.get("code") != 0:
                return {"ok": False, "http_status": res.status,
                        "business_code": parsed.get("code"),
                        "note": "旧会话或旧签名未通过业务校验；不自动重试。"}
            safe = snapshot(entry, parsed["data"])
            safe.update(source="single_captured_request_probe", live=True,
                        captured_at=datetime.now(TZ).isoformat())
            return {"ok": True, "snapshot": safe,
                    "note": "仅证明本次历史签名仍被接受，不代表已能生成新签名。"}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "http_status": exc.code,
                "note": "HTTP失败或重定向已阻止；未重试。"}
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return {"ok": False, "note": "网络、证书或响应结构异常；未重试，未输出敏感响应。"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="科大网球：离线分析/方案预览/单次只读探测")
    parser.add_argument("command", choices=("analyze", "snapshot", "preview", "probe"))
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--har")
    source.add_argument("--snapshot", help="仅用于 preview，读取 snapshot 或 probe 的安全输出")
    parser.add_argument("--config")
    parser.add_argument("--output")
    parser.add_argument("--proxy", help="可选本机 HTTP 代理；默认直连")
    args = parser.parse_args(argv)
    try:
        entries = read_har(args.har) if args.har else []
        if args.snapshot and args.command != "preview":
            raise InputError("--snapshot 仅支持 preview。")
        if args.command == "preview":
            if not args.config:
                raise InputError("preview 需要 --config 配置文件。")
            try:
                config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
                if args.snapshot:
                    saved = json.loads(Path(args.snapshot).read_text(encoding="utf-8-sig"))
                    snap = saved.get("snapshot", saved)
                    if not isinstance(snap, dict) or "days" not in snap:
                        raise ValueError()
                else:
                    entry, data = latest_venue(entries)
                    snap = snapshot(entry, data)
            except (OSError, ValueError, TypeError, AttributeError):
                raise InputError("无法读取配置或快照 JSON。") from None
            result = preview(snap, config)
        elif args.command == "analyze":
            result = analyze(entries)
        else:
            entry, data = latest_venue(entries)
            if args.command == "probe":
                result = probe(entry, args.proxy)
            elif args.command == "snapshot":
                result = snapshot(entry, data)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            out = Path(args.output)
            if out.resolve() in {Path(p).resolve() for p in (args.har, args.config, args.snapshot) if p}:
                raise InputError("输出文件不得覆盖输入 HAR 或配置。")
            out.write_text(text + "\n", encoding="utf-8")
            print("结果已保存。")
        else:
            print(text)
        return 1 if args.command == "probe" and not result["ok"] else 0
    except InputError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (KeyError, TypeError, ValueError, AttributeError, OSError):
        print("输入结构不符合预期或输出不可写；未输出原始数据。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
