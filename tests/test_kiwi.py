import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
from io import BytesIO
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flightbot import *
from kiwi_provider import *
DATA=json.loads(Path(__file__).with_name('kiwi-results.json').read_text())

class KiwiTests(unittest.TestCase):
    def setUp(self):
        self.clock=patch('flightbot.datetime',wraps=datetime);mock=self.clock.start()
        mock.now.return_value=datetime(2026,10,29,tzinfo=TZ);self.addCleanup(self.clock.stop)
        self.spec=Search('PL','PH',currency='PLN',stops=1)
    def test_real_response_mapping(self):
        r=parse_kiwi(DATA,self.spec);self.assertEqual((r.count,r.source,r.offers[0].price),(2,'Kiwi.com','1430'))
        self.assertIn('1 przesiadka',r.offers[0].summary);self.assertNotIn('/i',r.offers[0].summary);self.assertIn('19.11.2026',r.offers[0].summary);self.assertTrue(r.offers[0].key.startswith('kiwi:'))
    def test_country_window_and_stops(self):
        a=KiwiProvider().arguments(self.spec)
        self.assertEqual((a['flyFrom'],a['flyTo'],a['departureDate'],a['departureDateTo']),('PL','PH','29/10/2026','27/11/2026'))
        self.assertFalse(a['allow_self_transfer']);self.assertEqual(a['max_sector_stopovers'],1)
    def test_city_names_not_only_one_airport(self):
        a=KiwiProvider().arguments(Search('city:WAW','city:MNL'))
        self.assertEqual((a['flyFrom'],a['flyTo']),('Warsaw','Manila'))
    def test_return_window_and_stay(self):
        a=KiwiProvider().arguments(Search('PL','PH',nights_from=10,nights_to=14))
        self.assertEqual((a['returnDate'],a['returnDateTo'],a['nights_in_dst_from'],a['nights_in_dst_to']),('08/11/2026','11/12/2026',10,14))
    def test_fractional_budget_not_rounded_down(self):
        self.spec.budget='1430.50';self.assertEqual(KiwiProvider().arguments(self.spec)['price_to'],1431)
    def test_bad_data_rejected(self):
        for change in ['currency','count','price','url','stops','dates','missing','trip']:
            data=copy.deepcopy(DATA)
            if change=='currency':data['currency']='EUR'
            if change=='count':data['resultsCount']=99
            if change=='price':data['itineraries'][0]['price']='NaN'
            if change=='url':data['itineraries'][0]['bookingUrl']='https://evil.test/'
            if change=='stops':data['itineraries'][0]['outbound']['stops']=0
            if change=='dates':data['itineraries'][0]['outbound']['departureTime']='2027-11-19T10:30:00'
            if change=='missing':del data['itineraries'][0]['outbound']['segments']
            if change=='trip':data['itineraries'][0]['inbound']=data['itineraries'][0]['outbound']
            with self.subTest(change=change),self.assertRaises(ProviderError):parse_kiwi(data,self.spec)
    def test_price_and_booking_url_not_identity(self):
        data=copy.deepcopy(DATA);data['itineraries'][0].update(price=1400,bookingUrl='https://kiwi.com/u/another')
        self.assertEqual(parse_kiwi(DATA,self.spec).offers[0].key,parse_kiwi(data,self.spec).offers[0].key)
    def test_airport_route_enforced(self):
        with self.assertRaises(ProviderError):parse_kiwi(DATA,Search('GDN','MNL',currency='PLN'))
    def test_empty_response(self):
        self.assertEqual(parse_kiwi({'currency':'PLN','itineraries':[],'resultsCount':0},self.spec).offers,[])
    def test_curated_results_label(self):
        r=parse_kiwi(DATA,self.spec);text=render(r,self.spec)
        self.assertIn('Źródło: Kiwi.com',text);self.assertIn('lista może być niepełna',text);self.assertNotIn('100 wyników',text)
    def test_each_offer_has_own_booking_button(self):
        r=parse_kiwi(DATA,self.spec);reply=Bot(None,Store(':memory:')).results_reply('42',self.spec,r)
        links=[x['url'] for row in reply.markup['inline_keyboard'] for x in row if 'url' in x]
        self.assertEqual(links,[o.url for o in r.offers])
    def test_provider_change_resets_baseline_without_false_alerts(self):
        s=Store(':memory:');r=parse_kiwi(DATA,self.spec)
        s.add('42',self.spec,Result([],0,BASE,'old',False))
        class P:
            def search(self,spec):return r
        self.assertEqual(s.scan(P()),[])
        self.assertEqual(s.db.execute('select count(*) from outbox').fetchone()[0],0)
        self.assertEqual(s.db.execute('select source from snapshots').fetchone()[0],'Kiwi.com')
        changed=copy.deepcopy(r);changed.offers[0].price='1300'
        r=changed;s.scan(P());self.assertIn('Spadek',s.db.execute('select body from outbox').fetchone()[0])
    def test_protocol_sse_and_json(self):
        class Response(BytesIO):
            url=ENDPOINT
            def getheader(self,key,default=None):return 'text/event-stream' if key=='Content-Type' else default
        message={'jsonrpc':'2.0','id':1,'result':{'ok':'yes'}}
        body=('event: message\r\ndata: '+json.dumps(message)+'\r\n\r\n').encode()
        with patch('kiwi_provider.urlopen',return_value=Response(body)):
            self.assertEqual(KiwiProvider().rpc('tools/list',{}),{'ok':'yes'})
    def test_protocol_error(self):
        class Response(BytesIO):
            url=ENDPOINT
            def getheader(self,key,default=None):return 'application/json' if key=='Content-Type' else default
        with patch('kiwi_provider.urlopen',return_value=Response(b'{"jsonrpc":"2.0","id":1,"error":{}}')):
            with self.assertRaises(ProviderError):KiwiProvider().rpc('tools/list',{})
    def test_cache_and_refresh(self):
        p=KiwiProvider();calls=[]
        def rpc(method,args):
            calls.append(method)
            return {} if method=='initialize' else {'structuredContent':DATA}
        p.rpc=rpc;p.search(self.spec);p.search(self.spec)
        self.assertEqual(calls,['initialize','tools/call'])
        p.last_request=0;p.search(self.spec,refresh=True);self.assertEqual(len(calls),3)

if __name__=='__main__':unittest.main()
