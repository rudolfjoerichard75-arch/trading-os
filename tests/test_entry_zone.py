import copy
import unittest
from test_engine import e, t, row
from bosque_execution import entry_window, assess, ENTRY_ZONE_POLICY


class EntryZoneTests(unittest.TestCase):
    def sample(self, side='SELL'):
        tr = {'direction':'SELL','entry':4111.60,'sl':4119.26,
              'tp1':4105.60,'tp2':4096.27,'tp3':4088.61}
        if side == 'BUY':
            tr['direction'] = side
            for key in ('entry','sl','tp1','tp2','tp3'): tr[key] = 8200-tr[key]
        return tr

    def test_example_and_mirrored_buy_are_capped(self):
        w = entry_window(self.sample())
        self.assertEqual((w['low'],w['high'],w['width_pips']), (4111.60,4115.60,40))
        for side in ('BUY','SELL'):
            tr=self.sample(side); before=copy.deepcopy(tr); zone=entry_window(tr)
            self.assertLessEqual(zone['width_pips'],40)
            for price in (zone['low'],zone['high']):
                self.assertEqual(assess(tr,price)['status'],'PASS')
            self.assertEqual(tr,before)

    def test_narrow_valid_zone_not_widened(self):
        tr={'direction':'BUY','sl':100,'tp2':109}
        w=entry_window(tr)
        self.assertEqual((w['low'],w['high'],w['width_pips']),(102,103,10))

    def test_new_fills_obey_zone_but_legacy_fills_keep_rules(self):
        tr=self.sample()
        self.assertEqual(assess(tr,4116)['status'],'PASS')
        tr['parameters']={'entry_zone_policy':ENTRY_ZONE_POLICY}
        self.assertIn('FILL_OUTSIDE_ENTRY_ZONE',assess(tr,4116)['reasons'])
        for price in (4111.60,4115.60): self.assertEqual(assess(tr,price)['status'],'PASS')

    def test_paper_rejects_outside_zone_next_open(self):
        plan=self.sample()
        tr=t.new_trade({'id':'zone','direction':'SELL'},plan,'2026-10-21T00:00Z')
        t.advance(tr,row(at='2026-10-21T00:00Z',o=4116,h=4117,l=4115,c=4116))
        self.assertEqual(tr['result'],'NOT_ENTERED')
        self.assertEqual(tr['exit_reason'],'FILL_OUTSIDE_ENTRY_ZONE')

    def test_telegram_entry_is_range_without_duplicate_paragraph(self):
        text=e.format_telegram('SELL','SELL BREAKOUT RETEST',75,self.sample(),
                               'ASIAN',{'zone':'DISCOUNT'},{'atr_pips':32.4})
        self.assertIn('📍 Entry : 4111.60-4115.60',text)
        self.assertIn('Lebar zon: 40.0 pips',text)
        self.assertIn('Risk: 36.6–76.6 pips',text)
        self.assertIn('TP1: 4105.60 (60.0–100.0 pips)',text)
        self.assertNotIn('Julat entry risk/RR:',text)
        self.assertNotIn('SELL SELL',text)

    def test_unavailable_zone_does_not_format_actionable_alert(self):
        tr=self.sample();tr.update(sl=100,tp2=101,direction='BUY')
        with self.assertRaises(ValueError):
            e.format_telegram('BUY','BUY TEST',70,tr,'ASIAN',{'zone':'PREMIUM'},{})
