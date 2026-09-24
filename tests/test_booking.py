import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from booking import submit_once,has_overlap
from booking_protocol import BookingLedger
from tennis import InputError
class SubmitTests(unittest.TestCase):
    def setUp(self):
        self.payload={'day':1789660800,'kind_id':2,'share_open':1,'selected_block':[{'hour':23,'min':m,'venue_id':1,'venue':{'id':1,'sn':'1号','school_part':1}} for m in (0,15)],'selected_partner':[{'id':8,'avatar':'','nickname':'test','pinyin':'test','salary_no':'x','selected':True}]}
        self.session={'open_id':'own-account','token':'local-token','version':'1.0.0'}
        self.order={'kind_id':2,'book_date':1789660800,'book_block':json.dumps(self.payload['selected_block']),'partner_list':[{'partner_uid':8,'op_flag_name':'wait'}],'is_invite':False,'order_flag':20,'order_flag_name':'lock','order_flag_str_app':'待同伴确认'}
        self.noise=Mock(side_effect=lambda op,**kw:{'noise':'new-noise'} if op=='noise' else self.session)
    def run_case(self,post,query=None,orders=None):
        with tempfile.TemporaryDirectory() as d:
            ledger=BookingLedger(Path(d)/'ledger.db')
            output=submit_once(self.payload,self.session,'test-suffix',ledger,self.noise,post,query or Mock(),orders or Mock(return_value=[]))
            with self.assertRaises(InputError):submit_once(self.payload,self.session,'test-suffix',ledger,self.noise,post,query or Mock(),orders or Mock(return_value=[]))
            self.assertEqual(post.call_count,1)
            return output
    def test_timeout_never_reposts(self):
        r=self.run_case(Mock(side_effect=TimeoutError()))
        self.assertEqual(r['state'],'unknown')
        self.assertFalse(r['retry_allowed'])
    def test_malformed_response_never_reposts(self):
        self.assertEqual(self.run_case(Mock(return_value=[]))['state'],'unknown')
    def test_success_then_query_failure_never_reposts(self):
        r=self.run_case(Mock(return_value={'code':0,'data':{'order_no':'BO123'}}),Mock(side_effect=TimeoutError()))
        self.assertEqual(r['state'],'unknown')
    def test_timeout_reconciles_pending_without_second_post(self):
        r=self.run_case(Mock(side_effect=TimeoutError()),orders=Mock(return_value=[self.order]))
        self.assertEqual(r['state'],'pending_companion')
        self.assertTrue(r['reconciled_after_uncertain_result'])
    def test_normal_pending_verified(self):
        r=self.run_case(Mock(return_value={'code':0,'data':{'order_no':'BO123'}}),Mock(return_value={'order':self.order}))
        self.assertEqual(r['state'],'pending_companion')
    def test_noise_failure_and_changed_account_send_nothing(self):
        for provider in (Mock(side_effect=TimeoutError()),Mock(side_effect=[{'noise':'new'},dict(self.session,open_id='other')])):
            with tempfile.TemporaryDirectory() as d:
                post=Mock()
                with self.assertRaises(InputError):submit_once(self.payload,self.session,'test',BookingLedger(Path(d)/'l.db'),provider,post)
                post.assert_not_called()
    def test_overlap_blocks_active_but_not_cancelled(self):
        self.assertTrue(has_overlap([self.order],self.payload))
        cancelled=dict(self.order,order_flag=200,order_flag_name='cancel')
        self.assertFalse(has_overlap([cancelled],self.payload))

    def test_unknown_then_changed_court_or_overlap_still_one_post(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=BookingLedger(Path(d)/'ledger.db')
            post=Mock(side_effect=TimeoutError())
            submit_once(self.payload,self.session,'test',ledger,self.noise,post,Mock(),Mock(return_value=[]))
            changed=copy.deepcopy(self.payload)
            for b in changed['selected_block']:b['venue_id']=2;b['venue']['id']=2
            with self.assertRaises(InputError):submit_once(changed,self.session,'test',ledger,self.noise,post)
            changed['selected_block'][0]['min']=15
            changed['selected_block'][1]['min']=30
            with self.assertRaises(InputError):submit_once(changed,self.session,'test',ledger,self.noise,post)
            self.assertEqual(post.call_count,1)

    def test_database_failure_sends_nothing(self):
        import sqlite3
        ledger=Mock();ledger.reserve.side_effect=sqlite3.OperationalError('locked')
        post=Mock()
        with self.assertRaises(InputError):submit_once(self.payload,self.session,'test',ledger,self.noise,post)
        post.assert_not_called()