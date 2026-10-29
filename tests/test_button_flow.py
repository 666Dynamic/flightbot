"""Exercise the exact emitted buttons, without inventing callback payloads."""
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flightbot import *

class Provider:
    def __init__(self):self.specs=[];self.fail=False
    def search(self,spec,refresh=False):
        if self.fail:raise ProviderError('HTTP 403')
        self.specs.append(spec)
        return Result([],0,BASE+'?'+urlencode(spec.params()),datetime.now(timezone.utc).isoformat(),False)
    def locations(self,q):return [{'value':'city:ROM','text':'Rome (ROM), Italy','type':'city'}]

class ButtonFlow(unittest.TestCase):
    def setUp(self):self.s=Store(':memory:');self.p=Provider();self.b=Bot(self.p,self.s);self.r=self.b.handle('42','/start')
    def press(self,label):
        candidates=[x for row in self.r.markup['inline_keyboard'] for x in row if label in x['text'] and 'callback_data' in x]
        self.assertEqual(len(candidates),1,label)
        self.r=self.b.callback('42',candidates[0]['callback_data']);return self.r
    def flow(self,returning=False):
        self.press('Tam i z powrotem' if returning else 'W jedną stronę')
        self.press('Polska');self.press('Filipiny');self.press('Najbliższe 30 dni')
        if returning:self.press('10-14 nocy')
        self.press('Maks. 1');self.press('3000 zł')
    def test_oneway_entire_flow_no_typing(self):
        self.flow();self.assertIn('Podsumowanie',self.r);self.assertFalse(self.p.specs)
        self.press('Szukaj lotów');spec=self.p.specs[-1]
        self.assertEqual((spec.origin,spec.destination,spec.stops,spec.budget,spec.nights_from),('PL','PH',1,'3000',0))
    def test_return_entire_flow_no_typing(self):
        self.flow(True);self.press('Szukaj lotów');spec=self.p.specs[-1]
        self.assertEqual((spec.params()['trip'],spec.nights_from,spec.nights_to),('return',10,14))
    def test_short_places_list_and_custom(self):
        self.press('W jedną stronę')
        labels=[b['text'] for row in self.r.markup['inline_keyboard'] for b in row]
        self.assertTrue(all(x in labels for x in ('Polska','Niemcy','Europa')))
        self.assertFalse(any('Więcej' in x for x in labels))
        self.press('Inne miejsce');self.r=self.b.handle('42','Finlandia')
        self.assertEqual(self.s.wizard_get('42')['origin'],'FI')
        self.press('Manila');self.assertEqual(self.s.wizard_get('42')['destination'],'city:MNL')
    def test_text_not_interpreted_as_route_in_wizard(self):
        self.r=self.b.handle('42','z Polski do Filipin');self.assertIn('Wybierz przycisk',self.r)
        self.assertEqual(self.s.wizard_get('42')['step'],'trip');self.assertFalse(self.p.specs)
    def test_typing_place_also_works_without_extra_click(self):
        self.press('W jedną stronę');self.r=self.b.handle('42','Rzym')
        self.assertEqual(self.s.wizard_get('42')['origin'],'city:ROM');self.assertIn('Dokąd',self.r)
    def test_back_and_change_trip(self):
        self.press('Tam i z powrotem');self.press('Wstecz');self.press('W jedną stronę')
        self.assertEqual(self.s.wizard_get('42')['trip'],'oneway')
    def test_edit_trip_from_summary(self):
        self.flow();self.press('Typ/pobyt');self.press('Tam i z powrotem');self.press('7-10 nocy')
        self.assertIn('Podsumowanie',self.r);self.assertIn('7–10 nocy',self.r)
    def test_provider_failure_keeps_summary_and_offers_link(self):
        self.flow();self.p.fail=True;self.press('Szukaj lotów')
        self.assertIn('filtry są zachowane',self.r)
        self.assertEqual(self.s.wizard_get('42')['step'],'summary')
        links=[x['url'] for row in self.r.markup['inline_keyboard'] for x in row if 'url' in x]
        self.assertEqual(parse_qs(urlparse(links[0]).query)['to'],['PH'])
        self.p.fail=False;self.press('Szukaj lotów');self.assertEqual(len(self.p.specs),1)
    def test_old_unscoped_buttons_are_rejected(self):
        reply=self.b.callback('42','wz:t:return');self.assertIn('starszego',reply)
        self.assertEqual(self.s.wizard_get('42')['trip'],'')
    def test_same_place_is_rejected(self):
        self.press('W jedną stronę');self.press('Polska')
        self.press('Inne miejsce');self.r=self.b.handle('42','Polska');self.assertIn('różne miejsca',self.r)
        self.assertEqual(self.s.wizard_get('42')['destination'],'')
    def test_old_state_migrates_to_first_question(self):
        self.s.wizard_set('42',{'step':'origin','choices':[]})
        reply=self.b.callback('42','wz:new');self.assertIn('Krok 1',reply)
    def test_back_at_every_step_for_both_trip_types(self):
        for returning in (False,True):
            with self.subTest(returning=returning):
                self.r=self.b.handle('42','/start')
                sequence=[('Tam i z powrotem' if returning else 'W jedną stronę','origin'),('Polska','destination'),('Filipiny','when'),('Najbliższe 14 dni','nights' if returning else 'stops')]
                if returning:sequence.append(('10-14 nocy','stops'))
                sequence += [('Maks. 2','budget'),('2000 zł','summary')]
                for label,step in sequence:
                    previous=self.s.wizard_get('42')['step']
                    self.press(label);self.assertEqual(self.s.wizard_get('42')['step'],step)
                    self.press('Wstecz');self.assertEqual(self.s.wizard_get('42')['step'],previous)
                    self.press(label);self.assertEqual(self.s.wizard_get('42')['step'],step)
    def test_back_from_custom_returns_picker_without_advancing(self):
        self.press('W jedną stronę');self.press('Inne miejsce');self.press('Wróć do wyboru')
        self.assertEqual(self.s.wizard_get('42')['step'],'origin')
        self.assertTrue(any(b['text']=='Polska' for row in self.r.markup['inline_keyboard'] for b in row));self.press('Wstecz')
        self.assertEqual(self.s.wizard_get('42')['step'],'trip')
    def test_duplicate_back_does_not_skip_steps(self):
        self.press('W jedną stronę');self.press('Polska')
        old=next(b['callback_data'] for row in self.r.markup['inline_keyboard'] for b in row if 'Wstecz' in b['text'])
        self.r=self.b.callback('42',old)
        self.assertEqual(self.s.wizard_get('42')['step'],'origin')
        reply=self.b.callback('42',old)
        self.assertIn('starszego kroku',reply)
        self.assertEqual(self.s.wizard_get('42')['step'],'origin')
    def test_all_currencies_reach_provider_and_summary(self):
        for currency in ('PLN','EUR','USD'):
            with self.subTest(currency=currency):
                self.r=self.b.handle('42','/start')
                for label in ('W jedną stronę','Polska','Filipiny','Najbliższe 14 dni','Maks. 2',currency):self.press(label)
                self.press('2000 zł' if currency=='PLN' else '500 '+currency)
                self.assertIn(currency,self.r);self.press('Szukaj lotów')
                self.assertEqual(self.p.specs[-1].currency,currency)
    def test_currency_change_resets_old_budget_and_preserves_route(self):
        self.flow();self.press('Budżet');self.press('EUR')
        state=self.s.wizard_get('42')
        self.assertEqual((state['budget'],state['origin'],state['destination']),('','PL','PH'))
        self.press('Własna kwota');self.r=self.b.handle('42','200 zł')
        self.assertIn('Wybrana waluta to EUR',self.r)
        self.assertEqual(self.s.wizard_get('42')['step'],'budget')
        self.r=self.b.handle('42','250,50 EUR');self.assertIn('250.5 EUR',self.r)
        self.press('Szukaj lotów');self.assertEqual(self.p.specs[-1].currency,'EUR')
    def test_return_search_in_usd_and_edit_back(self):
        self.flow(True);self.press('Budżet');self.press('USD');self.press('1000 USD')
        self.assertIn('Podsumowanie',self.r);self.press('Skąd');self.press('Podsumowanie')
        self.assertIn('Podsumowanie',self.r);self.press('Szukaj lotów')
        spec=self.p.specs[-1];self.assertEqual((spec.currency,spec.nights_from,spec.nights_to),('USD',10,14))
    def test_cancel_switch_to_return_before_nights_restores_valid_summary(self):
        self.flow();self.press('Typ/pobyt');self.press('Tam i z powrotem')
        self.assertIn('Ile nocy',self.r);self.press('Podsumowanie')
        self.assertIn('Podsumowanie',self.r)
        state=self.s.wizard_get('42');self.assertEqual(state['trip'],'oneway')
        self.press('Szukaj lotów');self.assertEqual(self.p.specs[-1].nights_from,0)
    def test_restart_preserves_state(self):
        self.press('W jedną stronę');old=self.r
        self.b=Bot(self.p,self.s);self.press('Polska');self.assertIn('Dokąd',self.r)

if __name__=='__main__':unittest.main()
