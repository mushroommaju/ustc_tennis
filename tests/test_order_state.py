import copy,json,unittest
from order_state import matches_order,safe_order
from tennis import InputError
class OrderTests(unittest.TestCase):
    def setUp(self):
        self.payload={'kind_id':2,'day':1789660800,'selected_block':[{'hour':23,'min':0,'venue_id':1},{'hour':23,'min':15,'venue_id':1}],'selected_partner':[{'id':8}]}
        self.order={'kind_id':2,'book_date':1789660800,'book_block':json.dumps(self.payload['selected_block']),'partner_list':[{'partner_uid':8,'op_flag_name':'wait'}],'is_invite':False,'order_flag':20,'order_flag_name':'lock','order_flag_str_app':'待同伴确认','amount_str':'0.00'}
    def test_pending_is_not_success(self):
        result=safe_order(self.order,self.payload)
        self.assertEqual(result['state'],'pending_companion')
        self.assertFalse(result['all_companions_confirmed'])
    def test_wrong_day_friend_court_rejected(self):
        for key,value in [('book_date',0),('partner_list',[{'partner_uid':9}]),('book_block','[]'),('is_invite',True)]:
            other=copy.deepcopy(self.order);other[key]=value
            self.assertFalse(matches_order(other,self.payload))
            with self.assertRaises(InputError):safe_order(other,self.payload)
    def test_unseen_status_not_assumed_success(self):
        self.order['order_flag_name']='unknown'
        self.assertEqual(safe_order(self.order,self.payload)['state'],'unclassified')

    def test_success_with_waiting_friend_is_not_confirmed(self):
        self.order.update(order_flag=100,order_flag_name='success')
        self.assertEqual(safe_order(self.order,self.payload)['state'],'unclassified')
