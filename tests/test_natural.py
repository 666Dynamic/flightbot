import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flightbot import *
HTML=Path(__file__).with_name("results.html").read_text()

class Provider:
    def __init__(self):self.searched=[];self.queries=[]
    def search(self,spec,refresh=False):self.searched.append(spec);return parse_results(HTML)
    def locations(self,term):
        self.queries.append(term)
        return {
            "Warsaw":[{"value":"city:WAW","text":"Warsaw (WAW), Poland","type":"city"},{"value":"WAW","text":"Warsaw Chopin (WAW), Poland","type":"airport"}],
            "Gdańsk":[{"value":"city:GDN","text":"Gdańsk (GDN), Poland","type":"city"},{"value":"GDN","text":"Gdańsk Lech Wałęsa (GDN), Poland","type":"airport"}],
            "hamburga":[{"value":"HH_DE","text":"Hamburg, Germany","type":"subdivision"},{"value":"city:HAM","text":"Hamburg (HAM), Germany","type":"city"},{"value":"HAM","text":"Hamburg (HAM), Germany","type":"airport"}],
            "manili":[{"value":"city:MNL","text":"Manila (MNL), Philippines","type":"city"},{"value":"city:MII","text":"Marília (MII), Brazil","type":"city"}],
            "santiago":[{"value":"city:SCL","text":"Santiago (SCL), Chile","type":"city"},{"value":"city:SCQ","text":"Santiago (SCQ), Spain","type":"city"}],
            "islandii":[{"value":"IS","text":"Iceland","type":"country"}],
        }.get(term,[])

class NaturalTests(unittest.TestCase):
    def setUp(self):self.p=Provider();self.s=Store(":memory:");self.b=Bot(self.p,self.s)
    def test_user_philippines_no_dates(self):
        reply=self.b.handle("42","wyszukaj mi loty z Polski do Filipin najtańsze")
        s=self.p.searched[-1];self.assertEqual((s.origin,s.destination,s.days,s.currency),("PL","PH",30,"PLN"))
        self.assertIn("następne 30 dni",reply)
    def test_user_warsaw_manila(self):
        self.b.handle("42","najtańsze loty z Warszawy do Manili")
        s=self.p.searched[-1];self.assertEqual((s.origin,s.destination),("city:WAW","city:MNL"))
    def test_user_gdansk_hamburg(self):
        self.b.handle("42","z Gdańska do Hamburga")
        s=self.p.searched[-1];self.assertEqual((s.origin,s.destination),("city:GDN","city:HAM"))
    def test_budget_stay_and_stops_preserved(self):
        self.b.handle("42","pokaż loty z Warszawy do Manili w następnych 30 dniach do 4000 PLN, pobyt 10-16 nocy, maksymalnie 1 przesiadka")
        s=self.p.searched[-1];self.assertEqual((s.budget,s.stops,s.nights_from,s.nights_to),("4000",1,10,16))
    def test_ambiguity_asks_before_search(self):
        reply=self.b.handle("42","znajdź mi loty z Polski do Santiago")
        self.assertEqual(self.p.searched,[]);self.assertEqual(len(reply.markup["inline_keyboard"]),2)
        data=reply.markup["inline_keyboard"][1][0]["callback_data"]
        self.b.callback("42",data);self.assertEqual(self.p.searched[-1].destination,"city:SCQ")
    def test_ambiguous_choice_owned_by_chat(self):
        r=self.b.handle("42","loty z Polski do Santiago")
        self.b.callback("99",r.markup["inline_keyboard"][0][0]["callback_data"])
        self.assertEqual(self.p.searched,[])
    def test_watch_intent_preserved_on_choice(self):
        r=self.b.handle("42","obserwuj loty z Polski do Santiago")
        self.b.callback("42",r.markup["inline_keyboard"][0][0]["callback_data"])
        self.assertEqual(len(self.s.watches("42")),1)
    def test_no_match_clear_error(self):
        r=self.b.handle("42","loty z Polski do Nigdziowa")
        self.assertIn("Nie znalazłem",r);self.assertEqual(self.p.searched,[])
    def test_unknown_filter_not_silently_dropped(self):
        r=self.b.handle("42","loty z Polski do Filipin z bagażem")
        self.assertEqual(self.p.searched, [])
        self.assertIn("bagaż",r)
    def test_next_month(self):
        _,s=parse_command("loty z Polski do Filipin w przyszłym miesiącu")
        self.assertTrue(s.start.endswith("-01"));self.assertTrue(s.end)
    def test_no_route_prompts_for_example(self):
        # New error message doesn't contain "Napisz np." but has helpful examples
        r = self.b.handle("42","wyszukaj mi najtańsze loty")
        self.assertIn("Nie rozumiem, skąd i dokąd chcesz polecieć", r)
        self.assertIn("Przykłady:", r)
    def test_slash_compatibility(self):
        _,s=parse_command("/szukaj PL TH 30 500 1")
        self.assertIs(type(s),Search);self.assertEqual(s.currency,"USD")
    def test_natural_cannot_be_sent_unresolved(self):
        _,s=parse_command("loty z Warszawy do Manili")
        with self.assertRaises(ValueError):s.params()
    def test_no_arbitrary_url_identifier(self):
        with self.assertRaises(ValueError):Search("https://bad.test","PH")
        self.assertEqual(Search("city:WAW","city:MNL").params()["from"],"city:WAW")

if __name__=="__main__":unittest.main()
