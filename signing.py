"""Verified client signing and read-only fresh GET; no booking/cloud invocation."""
import argparse
import copy
import hashlib
import hmac
import json
from pathlib import Path
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import tennis


def validate_value(value):
    if value is None or isinstance(value, (str, bool)):
        return
    if type(value) is int and abs(value) <= 9007199254740991:
        return
    if isinstance(value, list):
        for item in value:
            validate_value(item)
        return
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        for item in value.values():
            validate_value(item)
        return
    raise tennis.InputError("签名只支持已验证的字符串、布尔、整数及JSON对象/数组。")


def js_value(value):
    validate_value(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def sign(parameters, suffix):
    if not isinstance(suffix, str) or not suffix:
        raise tennis.InputError("缺少本地签名配置。")
    parts = []
    for key in sorted(parameters):
        value = parameters[key]
        if key == "sign" or value is None:
            continue
        text = js_value(value)
        if text:
            parts.append(key + "=" + text)
    return hashlib.md5(("&".join(parts) + suffix).encode("utf-8")).hexdigest()


def verify(entries, suffix):
    count, mismatches = 0, []
    for index, entry in enumerate(entries, 1):
        if not tennis.target(entry):
            continue
        p = tennis.params(entry)
        if "sign" not in p:
            continue
        count += 1
        if not hmac.compare_digest(sign(p, suffix), p["sign"]):
            mismatches.append(index)
    return {"checked": count, "mismatch_entries": mismatches,
            "ok": count > 0 and not mismatches}


def latest_query_session(entries):
    """Return the newest successful tennis query only when its account is unambiguous.

    This is deliberately stricter than ``tennis.latest_venue``.  Historical
    snapshots may use the last successful response, while a live request must
    never silently fall back past a newer failed query or combine identities
    from one HAR capture.
    """
    queries = []
    for entry in entries:
        request = entry.get("request", {})
        url = urlsplit(request.get("url", ""))
        if (tennis.target(entry) and request.get("method") == "GET"
                and url.path == tennis.VENUE):
            queries.append(entry)
    if not queries:
        raise tennis.InputError("HAR 中没有网球查场请求，不能生成在线查询。")

    latest = queries[-1]
    try:
        latest_response = tennis.response_json(latest)
    except tennis.InputError:
        raise tennis.InputError("最新网球查场响应不可解析，停止在线查询。") from None
    if latest.get("response", {}).get("status") != 200 or latest_response.get("code") != 0:
        raise tennis.InputError("最新网球查场未成功，停止在线查询。")

    account_ids = set()
    for query in queries:
        values = tennis.params(query)
        open_id = values.get("open_id")
        if not isinstance(open_id, str) or not open_id:
            raise tennis.InputError("网球查场会话缺少账号标识，停止在线查询。")
        account_ids.add(open_id)
    if len(account_ids) != 1:
        raise tennis.InputError("HAR 包含多个网球查场账号，停止在线查询。")

    authorization = tennis.header(latest, "Authorization")
    if (not isinstance(authorization, str)
            or not authorization.startswith("Bearer ")
            or not authorization.removeprefix("Bearer ").strip()):
        raise tennis.InputError("最新网球查场缺少有效登录会话，停止在线查询。")
    return latest


def fresh_query(entry, suffix, now_ms=None):
    url = urlsplit(entry["request"]["url"])
    if (not tennis.target(entry) or entry["request"]["method"] != "GET" or
            url.path != tennis.VENUE or url.port not in (None, 443) or
            url.username or url.password or url.fragment):
        raise tennis.InputError("只允许生成网球查场GET请求。")
    pairs = parse_qsl(url.query, keep_blank_values=True)
    if len(dict(pairs)) != len(pairs):
        raise tennis.InputError("请求含重复参数，停止生成。")
    p = dict(pairs)
    if set(p) != {"open_id", "version", "timestamp", "sign"}:
        raise tennis.InputError("查询参数结构与已验证样本不同。")
    if not p["open_id"] or not tennis.header(entry, "Authorization"):
        raise tennis.InputError("缺少正常登录会话，需要新的HAR。")
    p["timestamp"] = int(time.time() * 1000) if now_ms is None else now_ms
    if type(p["timestamp"]) is not int or p["timestamp"] <= 0:
        raise tennis.InputError("时间戳必须是正整数毫秒。")
    p["sign"] = sign(p, suffix)
    out = copy.deepcopy(entry)
    out["request"]["url"] = urlunsplit((url.scheme, url.netloc, url.path, urlencode(p), ""))
    out["request"]["queryString"] = [{"name": k, "value": str(v)} for k, v in p.items()]
    return out


def main():
    parser = argparse.ArgumentParser(description="验证签名或使用新时间戳、新签名进行单次查场")
    parser.add_argument("command", choices=("verify", "query"))
    parser.add_argument("--har", required=True)
    parser.add_argument("--signing-config", default=str(Path(__file__).with_name("signing.local.json")))
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        if args.output and Path(args.output).resolve() in {Path(args.har).resolve(), Path(args.signing_config).resolve()}:
            raise tennis.InputError("输出不能覆盖HAR或签名配置。")
        suffix = json.loads(Path(args.signing_config).read_text(encoding="utf-8-sig"))["suffix"]
        entries = tennis.read_har(args.har)
        result = verify(entries, suffix)
        if args.command == "query":
            if not result["ok"]:
                raise tennis.InputError("抓包签名未全部验证通过，未发送请求。")
            entry = latest_query_session(entries)
            result = tennis.probe(fresh_query(entry, suffix))
            result["fresh_signature"] = True
            if result["ok"]:
                result["snapshot"]["source"] = "fresh_signed_get"
                result["note"] = "新时间戳及新签名查场成功；仅验证此会话和此GET，不代表预约提交已可用。"
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
            print(json.dumps({"ok": result["ok"], "fresh_signature": result.get("fresh_signature"),
                              "http_status": result.get("http_status"), "business_code": result.get("business_code")}, ensure_ascii=False))
        else:
            print(text)
        return 0 if result["ok"] else 1
    except tennis.InputError as exc:
        print(str(exc))
        return 2
    except (OSError, ValueError, KeyError, TypeError):
        print("输入或网络数据不符合预期，未输出原始凭据。")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
