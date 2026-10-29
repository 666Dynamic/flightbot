"""Regressions found by reviewing live location responses and full conversations."""
import sys
import unittest
from pathlib import Path
from datetime import date
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flightbot import *

class Provider:
    def __init__(self):self.searched=[];self.queries=[];self.place_labels={}
    def search(self,spec,refresh=False):
        self.searched.append(spec)
        return Result([],0,BASE,datetime.now(timezone.utc).isoformat(),False)
    def locations(self,query):
        self.queries.append(query)
        result={
            'Warsaw':[{'value':'city:WAW','text':'Warsaw (WAW), Poland','type':'city'}],
            'manila':[{'value':'city:MNL','text':'Manila (MNL), Philippines','type':'city'}],
            'Wrocław':[{'value':'city:WRO','text':'Wrocław (WRO), Poland','type':'city'}],
            'berlina':[{'value':'city:BER','text':'Berlin (BER), Germany','type':'city'}],
            'Berlin':[{'value':'city:BER','text':'Berlin (BER), Germany','type':'city'}],
            'York':[{'value':'city:NYC','text':'New York (NYC), United States','type':'city'}],
            'region':[{'value':'north-america','text':'North America','type':'continent'}],
        }.get(query,[])
        self.place_labels.update({o['value']:o['text'] for o in result})
        return result

class Regressions(unittest.TestCase):
    def setUp(self):self.p=Provider();self.s=Store(':memory:');self.b=Bot(self.p,self.s)
    def test_noise_without_z_does_not_become_city(self):
        r=self.b.handle('42','wyszukaj mi loty Warszawa do Manila')
        self.assertEqual((self.p.searched[-1].origin,self.p.searched[-1].destination),('city:WAW','city:MNL'))
    def test_wroclaw_declension_calls_canonical_provider_name(self):
        self.b.handle('42','loty z Wrocławia do Berlina')
        self.assertEqual(self.p.searched[-1].origin,'city:WRO')
        self.assertIn('Wrocław',self.p.queries)
    def test_fuzzy_hit_requires_confirmation(self):
        code,choices=location_choices(self.p,'York')
        self.assertIsNone(code);self.assertEqual(choices[0]['value'],'city:NYC')
    def test_chosen_region_not_queried_again(self):
        r=self.b.handle('42','loty z Polski do region')
        self.b.callback('42',r.markup['inline_keyboard'][0][0]['callback_data'])
        self.assertEqual(self.p.searched[-1].destination,'north-america')
        self.assertEqual(self.p.queries,['region'])
    def test_direct_synonyms(self):
        for term in ['bezpośrednio','bezposrednio','bez przesiadek','direct']:
            with self.subTest(term=term):self.assertEqual(parse_command('z Polski do Filipin '+term)[1].stops,0)
    def test_currency_symbols_and_grouped_amount(self):
        for text,budget,currency in [('do 500 €','500','EUR'),('do 500 $','500','USD'),('do 4 000,50 zł','4000.50','PLN')]:
            spec=parse_command('z Polski do Filipin '+text)[1]
            self.assertEqual((spec.budget,spec.currency),(budget,currency))
    def test_unknown_filters_not_executed(self):
        for text in ['z Polski do Filipin z bagażem','z Polski do Filipin bez nocnych przesiadek','z Polski do Filipin dla 2 osób']:
            with self.subTest(text=text):
                self.assertIn('Nie udało się',self.b.handle('42',text));self.assertEqual(self.p.searched,[])
    def test_ambiguous_na_dni_requires_clarification(self):
        with self.assertRaisesRegex(ValueError,'długość pobytu'):parse_command('z Polski do Filipin na 7 dni')
    def test_named_month(self):
        spec=parse_command('z Polski do Filipin w listopadzie 2027')[1]
        self.assertEqual((spec.start,spec.end),('2027-11-01','2027-11-30'))
    def test_date_range_before_route_extraction(self):
        spec=parse_command('z Polski do Filipin od 20.12.2027 do 05.01.2028')[1]
        self.assertEqual((spec.destination,spec.start,spec.end),('PH','2027-12-20','2028-01-05'))
    def test_dates_respect_injected_today(self):
        self.assertEqual(parse_date_range('20.12-05.01',date(2030,2,1)),('2030-12-20','2031-01-05'))
        self.assertEqual(parse_date_range('20.12.2030-05.01',date(2029,2,1)),('2030-12-20','2031-01-05'))
    def test_iso_dates(self):
        self.assertEqual(parse_date_range('2030-12-20 – 2031-01-05',date(2030,1,1)),('2030-12-20','2031-01-05'))
    def test_invalid_and_conflicting_filters_rejected(self):
        for text in ['od 31.02.2027 do 05.03.2027','w przyszłym miesiącu w następnych 30 dniach','bez przesiadek maks 2 przesiadki','do 500 EUR w USD','pobyt 0 nocy','w następnych 10 dniach w następnych 20 dniach']:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):parse_command('z Polski do Filipin '+text)
    def test_slash_extra_arguments_not_ignored(self):
        with self.assertRaises(ValueError):parse_command('/szukaj PL PH 30 500 1 PLN 3-5 extra')
    def test_malformed_locations_fail_as_provider_error(self):
        for body in ['not json','[]','{}','{"results":{}}']:
            p=Flightlist();p.get=lambda *args,body=body:body
            with self.subTest(body=body):
                with self.assertRaises(ProviderError):p.locations('Rome')
    def test_new_route_bypasses_and_clears_wizard(self):
        self.b.handle('42','/kreator')
        self.b.handle('42','Polska → Filipiny')
        self.assertEqual(self.p.searched[-1].destination,'PH');self.assertIsNone(self.s.wizard_get('42'))
    def test_old_step_button_does_not_move_wizard(self):
        self.b.handle('42','/kreator');self.b.callback('42','wz:o:PL')
        self.b.callback('42','wz:o:city:WAW')
        data=self.s.wizard_get('42');self.assertEqual((data['step'],data['origin']),('destination','PL'))
    def test_previous_session_button_rejected(self):
        r=self.b.handle('42','/kreator');old=r.markup['inline_keyboard'][0][0]['callback_data']
        self.b.handle('42','/kreator');reply=self.b.callback('42',old)
        self.assertIn('starszego kroku',reply);self.assertEqual(self.s.wizard_get('42')['origin'],'')
    def test_negative_pick_not_allowed(self):
        self.b.handle('42','/kreator');self.b.handle('42','York')
        reply=self.b.callback('42','wz:pick:-1');self.assertIn('Nieaktualny',reply)
        self.assertEqual(self.s.wizard_get('42')['origin'],'')
    def test_go_and_edit_incomplete_do_not_crash(self):
        self.b.handle('42','/kreator')
        for command in ['wz:go','wz:ed:budget','wz:edit']:
            reply=self.b.callback('42',command)
            self.assertFalse(self.p.searched)
        self.assertEqual(self.s.wizard_get('42')['step'],'origin')
    def test_malformed_telegram_response_redacted(self):
        from unittest.mock import patch
        from io import BytesIO
        for body in (b'[]', b'{}', b'{"ok":true}'):
            with self.subTest(body=body), patch('flightbot.urlopen',return_value=BytesIO(body)):
                with self.assertRaises(RuntimeError) as e:Telegram('secret-token').call('getMe',{})
                self.assertNotIn('secret-token',str(e.exception))
    def test_generated_buttons_fit_limit(self):
        reply=self.b.handle('42','/kreator')
        for row in reply.markup['inline_keyboard']:
            for item in row:self.assertLessEqual(len(item['callback_data'].encode()),64)

if __name__=='__main__':unittest.main()
