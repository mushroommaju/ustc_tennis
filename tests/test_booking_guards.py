import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from booking import list_orders,has_overlap,validate_report_path,ROOT
from tennis import InputError
class GuardTests(unittest.TestCase):
    def test_second_page_overlap_is_seen(self):
        row={'book_date':1789660800,'book_block':[{'hour':23,'min':0}],'order_flag_name':'lock','order_flag':20}
        with patch('booking.read_api',side_effect=[{'list':[dict(row,book_date=0)]},{'list':[row]},{'list':[]}]):
            orders=list_orders({},'x')
        self.assertTrue(has_overlap(orders,{'day':1789660800,'selected_block':[{'hour':23,'min':0}]}))
    def test_pagination_limit_fails_closed(self):
        with patch('booking.read_api',return_value={'list':[{}]}) as query:
            with self.assertRaises(InputError):list_orders({},'x')
            self.assertEqual(query.call_count,10)
    def test_report_cannot_overwrite_inputs(self):
        for name in ('config.json','config.local.json','account.local.json','signing.local.json','booking-ledger.sqlite3','booking.py'):
            with self.assertRaises(InputError):validate_report_path(ROOT/name,ROOT/'config.json')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'custom.json'
            with self.assertRaises(InputError):validate_report_path(path,path)
