"""Local runtime session/noise adapter and narrowly allowlisted signed API reads."""
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
import signing
from tennis import InputError

ROOT = Path(__file__).resolve().parent
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def bridge(operation, **kwargs):
    try:
        result = subprocess.run(['node', str(ROOT / 'runtime_bridge.cjs')], input=json.dumps({'operation': operation, **kwargs}), capture_output=True, text=True, encoding='utf-8', timeout=50, cwd=ROOT)
    except (subprocess.TimeoutExpired, OSError):
        raise InputError('运行时进程超时或不可用，未重试。') from None
    try:
        parsed = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise InputError('运行时未返回有效结果。') from None
    if result.returncode or not parsed.get('ok'):
        raise InputError('运行时连接失败，请检查目标小程序、登录和调试器。')
    return parsed['result']

def headers(session):
    if not session.get('token') or not session.get('open_id'):
        raise InputError('缺少当前登录会话。')
    return {'Authorization': 'Bearer '+session['token'], 'Platform':'MiniProgram', 'DeviceFingerprint':session.get('deviceFingerprint',''), 'Wx-Env':session.get('environment',''), 'Content-Type':'application/json'}

def read_api(path, session, suffix, extra=None):
    import re
    if path not in ('/api/venue/tennis','/api/order','/api/my/current') and not re.fullmatch(r'/api/order/result/BO[0-9]+',path):
        raise InputError('不允许的查询接口。')
    extra = extra or {}
    if set(extra) - ({'page','partner','scene'} if path == '/api/order' else set()):
        raise InputError('不允许的查询参数。')
    params = {**extra, 'open_id':session['open_id'], 'version':session['version'], 'timestamp':int(time.time()*1000)}
    params['sign'] = signing.sign(params, suffix)
    request = Request('https://sport.ustc.edu.cn'+path+'?'+urlencode(params), headers=headers(session), method='GET')
    try:
        with build_opener(NoRedirect).open(request,timeout=15) as response:
            result = json.load(response)
    except (HTTPError, URLError, ValueError, TimeoutError):
        raise InputError('实时查询失败，未重试；请检查登录、网络和服务状态。') from None
    if not isinstance(result,dict) or result.get('code') != 0:
        raise InputError('实时查询返回失败，未重试。')
    return result['data']

def fresh_venue(session, suffix):
    data = read_api('/api/venue/tennis',session,suffix)
    from datetime import datetime, timezone
    return {'startedDateTime':datetime.now(timezone.utc).isoformat()}, data
