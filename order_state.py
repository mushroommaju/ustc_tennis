"""Authoritative booking result matching; never treat POST acceptance as completion."""
import json
from tennis import InputError

def matches_order(order, payload):
    try:
        raw = order['book_block']
        blocks = json.loads(raw) if isinstance(raw,str) else raw
        key=lambda b:(b['hour'],b['min'],b['venue_id'])
        return (order['kind_id']==payload['kind_id'] and int(order['book_date'])==int(payload['day'])
                and [key(b) for b in blocks]==[key(b) for b in payload['selected_block']]
                and sorted(p['partner_uid'] for p in order['partner_list'])==sorted(p['id'] for p in payload['selected_partner'])
                and order.get('is_invite') is False)
    except (KeyError,TypeError,ValueError):
        return False

def safe_order(order, payload):
    if not matches_order(order,payload):
        raise InputError('查单结果与预约草案不一致，停止后续操作。')
    flag=order.get('order_flag_name')
    # Historical captures contain lock=20, success=100 and cancel=200.
    status={(20,'lock'):'pending_companion',(100,'success'):'confirmed',(200,'cancel'):'cancelled'}.get((order.get('order_flag'),flag),'unclassified')
    if status=='confirmed' and not all(p.get('op_flag_name')=='agree' for p in order['partner_list']):
        status='unclassified'
    return {'state':status,'server_status':order.get('order_flag_str_app'), 'order_flag':order.get('order_flag'),
            'order_flag_name':flag,'matches_request':True,'companion_count':len(order['partner_list']),
            'all_companions_confirmed':all(p.get('op_flag_name')=='agree' for p in order['partner_list']),
            'amount':order.get('amount_str')}
