import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
import bosque_push as p
from cryptography.hazmat.primitives.asymmetric import ec

class PushTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);(root/'engine').mkdir()
        for name,value in [('ROOT',root),('STATE',root/'engine/push_state.json'),('CONFIG',root/'push-config.json')]:
            patcher=patch.object(p,name,value);patcher.start();self.addCleanup(patcher.stop)
        self.at=datetime.now(timezone.utc)
        self.key=ec.generate_private_key(ec.SECP256R1())
        self.sub={'endpoint':'https://web.push.apple.com/test-private-endpoint','keys':{'auth':'secret','p256dh':'secret'}}
    def test_first_run_reserves_only_test_and_never_exposes_subscription(self):
        old={'signal_id':'old','strategy_version':'SCALPING V3','telegram_status':'SENT','timestamp':(self.at-timedelta(minutes=1)).isoformat()}
        p.write(p.ROOT/'engine/trade_journal.json',{'trades':[old]})
        p.reserve(self.at,'1',self.key,[self.sub])
        state=p.read(p.STATE,{})
        self.assertEqual(len(state['deliveries']),1)
        self.assertNotIn('test-private-endpoint',p.STATE.read_text())
        self.assertNotIn('secret',p.STATE.read_text())
    def test_dispatch_and_dedup(self):
        p.reserve(self.at,'1',self.key,[self.sub]);sent=[]
        def sender(**kwargs):
            sent.append(kwargs);return type('Response',(),{'status_code':201})()
        p.dispatch(self.at,'1',self.key,[self.sub],sender)
        p.reserve(self.at,'2',self.key,[self.sub]);p.dispatch(self.at,'2',self.key,[self.sub],sender)
        self.assertEqual(len(sent),1)
        self.assertEqual(sent[0]['ttl'],300)
    def test_real_encryption_with_mock_transport(self):
        from cryptography.hazmat.primitives import serialization
        import base64
        from unittest.mock import Mock
        enc=lambda b:base64.urlsafe_b64encode(b).decode().rstrip('=')
        receiver=ec.generate_private_key(ec.SECP256R1())
        self.sub['keys']={'p256dh':enc(receiver.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)), 'auth':enc(b'0123456789abcdef')}
        p.reserve(self.at,'1',self.key,[self.sub])
        with patch('requests.post',return_value=Mock(status_code=201)) as send:
            p.dispatch(self.at,'1',self.key,[self.sub])
        self.assertEqual(send.call_count,1)
        self.assertEqual(next(iter(p.read(p.STATE,{})['deliveries'].values()))['status'],'ACCEPTED_BY_PUSH_SERVICE')

    def test_interrupted_reservation_is_not_resent(self):
        p.reserve(self.at,'1',self.key,[self.sub])
        p.dispatch(self.at,'2',self.key,[self.sub],lambda **kw:self.fail('resent'))
        self.assertEqual(next(iter(p.read(p.STATE,{})['deliveries'].values()))['status'],'DELIVERY_UNKNOWN')
    def test_old_future_and_reconstructed_entries_blocked(self):
        trade={'signal_id':'x','strategy_version':'SCALPING V3','telegram_status':'SENT','direction':'BUY','planned_entry':1,'sl':0,'tp1':2,'tp2':3,'tp3':4}
        activation=(self.at-timedelta(days=1)).isoformat()
        for delta in [-601,60]:
            trade['timestamp']=(self.at+timedelta(seconds=delta)).isoformat()
            self.assertEqual(p.candidates({'trades':[trade]}, {},self.at,activation),[])
        trade['timestamp']=self.at.isoformat()
        self.assertEqual(len(p.candidates({'trades':[trade]}, {},self.at,activation)),1)
        trade['performance_group']='HISTORICAL_RECONSTRUCTION'
        self.assertEqual(p.candidates({'trades':[trade]}, {},self.at,activation),[])

if __name__=='__main__': unittest.main()
