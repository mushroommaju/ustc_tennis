import subprocess,unittest
from unittest.mock import patch
from runtime_client import bridge
from tennis import InputError
class RuntimeTests(unittest.TestCase):
    def test_timeout_becomes_safe_domain_error(self):
        with patch('runtime_client.subprocess.run',side_effect=subprocess.TimeoutExpired(['node'],50,output='private-session')):
            with self.assertRaises(InputError) as raised:bridge('session')
            self.assertNotIn('private-session',str(raised.exception))
    def test_invalid_bridge_output_does_not_leak(self):
        result=subprocess.CompletedProcess([],1,stdout='private-session',stderr='private-session')
        with patch('runtime_client.subprocess.run',return_value=result):
            with self.assertRaises(InputError) as raised:bridge('session')
            self.assertNotIn('private-session',str(raised.exception))
