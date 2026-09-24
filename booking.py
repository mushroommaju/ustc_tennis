"""Single-shot runtime-backed tennis booking. Default command is a read-only preview."""
import argparse
import json
import os
import tempfile
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
import api_draft
from booking_protocol import prepare_booking, booking_intent_hash, booking_slot_claims, BookingLedger
from order_state import matches_order, safe_order
from runtime_client import bridge, fresh_venue, read_api, NoRedirect
from tennis import InputError, TZ
from booking_config import load_config
ROOT=Path(__file__).resolve().parent

def post_once(prepared):
    request=Request('https://sport.ustc.edu.cn/api/venue/order',data=prepared['body'],headers=prepared['headers'],method='POST')
    with build_opener(NoRedirect).open(request,timeout=15) as response:
        return json.load(response)

def list_orders(session,suffix):
    orders=[]
    for page in range(1,11):
        value=read_api('/api/order',session,suffix,{'page':page,'scene':'all','partner':0})
        if not isinstance(value,dict) or not isinstance(value.get('list'),list):
            raise InputError('查单结构未知，禁止提交。')
        if not value['list']:
            return orders
        orders.extend(value['list'])
    raise InputError('订单超过查询上限或分页异常，无法排除重叠，停止提交。')

def has_overlap(orders,payload):
    desired={b['hour']*60+b['min'] for b in payload['selected_block']}
    for o in orders:
        if o.get('order_flag')==200 and o.get('order_flag_name')=='cancel':continue
        if int(o.get('book_date',0))!=int(payload['day']):continue
        blocks=json.loads(o['book_block']) if isinstance(o.get('book_block'),str) else o.get('book_block')
        if not isinstance(blocks,list):raise InputError('现有订单时间结构未知，禁止提交。')
        if desired.intersection(b['hour']*60+b['min'] for b in blocks):return True
    return False

def submit_once(payload,session,suffix,ledger,noise_provider=bridge,post=post_once,query=read_api,orders_reader=list_orders):
    intent=booking_intent_hash(payload,session)
    try:
        reserved=ledger.reserve(intent,int(time.time()*1000),booking_slot_claims(payload,session))
    except sqlite3.Error:
        raise InputError('本地预约记录不可用，禁止提交。') from None
    if not reserved:
        raise InputError('本地已有同一预约意图，禁止重复提交。')
    sent=False
    try:
        # The exact same payload goes into cloud noise and the final signed body.
        noise=noise_provider('noise',expectedOpenId=session['open_id'],payload=payload)['noise']
        current=noise_provider('session')
        if any(current.get(k)!=session.get(k) for k in ('open_id','token','version','deviceFingerprint','environment')):
            raise InputError('获取noise后会话变化，停止提交。')
        prepared=prepare_booking(payload,session,noise,suffix,int(time.time()*1000))
        sent=True
        reply=post(prepared)
        if not isinstance(reply,dict) or 'code' not in reply:
            raise InputError('提交响应无法确认。')
        if reply['code']!=0:
            ledger.mark(intent,'known_failed')
            return {'state':'rejected','business_code':reply['code'],'post_attempts':1}
        result=reply.get('data',{})
        order_no=result.get('order_no') or result.get('result')
        if not isinstance(order_no,str) or not re.fullmatch(r'BO[0-9]+',order_no):
            raise InputError('提交被接受但没有可验证订单号。')
        detail=query('/api/order/result/'+order_no,session,suffix)
        summary=safe_order(detail['order'],payload)
        ledger.mark(intent,'success' if summary['state']=='confirmed' else 'pending')
        return {**summary,'post_attempts':1}
    except Exception as failure:
        try:
            ledger.mark(intent,'unknown' if sent else 'known_failed')
        except sqlite3.Error:
            return {'state':'unknown' if sent else 'stopped','post_attempts':int(sent),'retry_allowed':False,'ledger_update_failed':True}
        if not sent:raise InputError('提交前检查失败，未发送预约请求。') from None
        # Reconcile once; never retry a POST after an ambiguous transport outcome.
        try:
            candidates=[o for o in orders_reader(session,suffix) if matches_order(o,payload) and o.get('order_flag_name')!='cancel']
            if len(candidates)==1:
                summary=safe_order(candidates[0],payload)
                ledger.mark(intent,'success' if summary['state']=='confirmed' else 'pending')
                return {**summary,'post_attempts':1,'reconciled_after_uncertain_result':True}
            reconciliation='no_unique_match'
        except Exception as query_failure:
            reconciliation='query_failed_'+type(query_failure).__name__
        return {'state':'unknown','post_attempts':1,'retry_allowed':False,'failure_type':type(failure).__name__,'reconciliation':reconciliation}

def load_context(config_path):
    config=load_config(config_path)
    today=datetime.now(TZ).date()
    if config.get('date') not in {today.isoformat(),(today+timedelta(days=1)).isoformat()}:
        raise InputError('仅允许今天或明天，过期配置必须更新。')
    ids=config.get('companion_ids')
    if not isinstance(ids,list) or not ids:raise InputError('配置必须明确已有好友ID。')
    suffix=json.loads((ROOT/'signing.local.json').read_text(encoding='utf-8-sig'))['suffix']
    session=bridge('session')
    if config.get('expected_open_id')!=session['open_id']:raise InputError('配置账号与当前微信不同。')
    identity=read_api('/api/my/current',session,suffix)
    if identity.get('open_id')!=session['open_id']:raise InputError('业务账号与微信会话不一致。')
    entry,data=fresh_venue(session,suffix)
    payload=api_draft.build_unsigned(entry,data,config,ids)
    if has_overlap(list_orders(session,suffix),payload):raise InputError('已有重叠时段订单，禁止创建新的测试预约。')
    return config,payload,session,suffix

def validate_report_path(output,config_path):
    path=Path(output).resolve()
    protected={Path(config_path).resolve(),Path(config_path).resolve().with_name('account.local.json')}
    protected.update((ROOT/name).resolve() for name in ('config.local.json','config.json','account.local.json','signing.local.json','booking-ledger.sqlite3'))
    if path.suffix.lower()!='.json' or path in protected:
        raise InputError('报告必须是JSON且不能覆盖配置、账号、签名或预约账本。')
    if not path.parent.is_dir():
        raise InputError('报告目录不存在，请先选择已有目录。')
    return path

def write_report(path,report):
    name=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',suffix='.tmp',dir=path.parent,delete=False) as output:
            name=output.name
            json.dump(report,output,ensure_ascii=False,indent=2)
        os.replace(name,path)
    finally:
        if name and Path(name).exists():Path(name).unlink()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['check-config','preview','submit'])
    parser.add_argument('--config',default=str(ROOT/'config.json'))
    parser.add_argument('--report')
    args=parser.parse_args()
    try:
        if args.command=='check-config':
            config=load_config(args.config)
            print(json.dumps({'state':'config_valid','date':config['date'],'start':config['start'],'end':config['end'],'court_ids':config['court_priority'],'companion_count':config['companion_count'],'post_attempts':0},ensure_ascii=False))
            return 0
        report_path=validate_report_path(args.report or ROOT/('preview.latest.json' if args.command=='preview' else 'booking-validation.json'),args.config)
        start=time.monotonic()
        config,payload,session,suffix=load_context(args.config)
        report={'date':config['date'],'start':config['start'],'end':config['end'],'court_id':payload['selected_block'][0]['venue_id'],'companion_count':len(payload['selected_partner'])}
        if args.command=='preview':report.update(state='preview',post_attempts=0)
        else:report.update(submit_once(payload,session,suffix,BookingLedger(ROOT/'booking-ledger.sqlite3')))
        report['elapsed_ms']=round((time.monotonic()-start)*1000)
        try:
            write_report(report_path,report)
        except OSError:
            report['report_saved']=False
        print(json.dumps(report,ensure_ascii=False))
        return 0 if report['state'] in ('preview','pending_companion','confirmed') else 1
    except InputError as error:
        print(json.dumps({'state':'stopped','message':str(error)},ensure_ascii=False))
        return 2
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
        print(json.dumps({'state':'stopped','message':'运行条件不满足或数据异常，未自动重试。'},ensure_ascii=False))
        return 2
if __name__=='__main__':raise SystemExit(main())
