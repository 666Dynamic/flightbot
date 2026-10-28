import json
from pathlib import Path
import sys
import unittest
from datetime import date
from dataclasses import asdict,replace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flightbot import *
RT=Path(__file__).with_name("roundtrip-pln.html").read_text()
OW=Path(__file__).with_name("results.html").read_text()

class RoundtripTests(unittest.TestCase):
    def test_short_lowercase_command(self):
        _,s=parse_command("/szukaj pl de")
        self.assertEqual((s.origin,s.destination,s.days),("PL","DE",30))
    def test_user_philippines_and_manila_examples(self):
        for place,code in (("Filipin","PH"),("Manili","manili")):
            _,s=parse_command(f"pokaż loty z Polski do {place} w następnych 30 dniach do 500 USD")
            self.assertEqual(s.destination,code)
    def test_real_roundtrip_prices(self):
        r=parse_results(RT,expected_currency="PLN")
        self.assertEqual(len(r.offers),2);self.assertEqual(r.offers[0].price,"2756")
        self.assertIn("Powrót:",offer_line(r.offers[0]))
        self.assertIn("24.11.2026",offer_line(r.offers[0]));self.assertIn("19:30",offer_line(r.offers[0]))
    def test_rollover_stay_window(self):
        p=Search("PL","TH",30,currency="PLN",nights_from=10,nights_to=16).params(date(2026,12,15))
        self.assertEqual((p["trip"],p["return_from"],p["return_to"]),("return","2026-12-25","2027-01-29"))
        self.assertEqual((p["nights_from"],p["nights_to"]),(10,16))
    def test_old_command_stays_usd_oneway(self):
        _,s=parse_command("/szukaj PL TH 30 500 1")
        self.assertEqual(s.currency,"USD");self.assertEqual(s.params()["trip"],"oneway")
    def test_new_command(self):
        action,s=parse_command("/obserwuj PL TH 30 4000 1 PLN 10-16")
        self.assertEqual(action,"watch");self.assertEqual((s.currency,s.nights_from,s.nights_to),("PLN",10,16))
    def test_polish_roundtrip(self):
        _,s=parse_command("pokaż loty z Polski do Tajlandii w następnych 30 dniach do 4000 zł, tam i z powrotem, pobyt 10-16 nocy, maksymalnie 1 przesiadka")
        self.assertEqual((s.currency,s.stops,s.nights_to),("PLN",1,16))
    def test_reject_unparsed_filters(self):
        # "z bagażem" is now silently ignored (lenient parsing) - no error, search proceeds
        _, s = parse_command("pokaż loty z Polski do Tajlandii w następnych 30 dniach z bagażem")
        self.assertIs(type(s), NaturalSearch)
        # "tam i z powrotem" without "pobyt" should still raise error
        with self.assertRaises(ValueError): parse_command("pokaż loty z Polski do Tajlandii w następnych 30 dniach tam i z powrotem")
    def test_invalid_stay_currency(self):
        for kw in ({"currency":"GBP"},{"nights_from":10},{"nights_from":16,"nights_to":10},{"nights_from":1,"nights_to":91}):
            with self.assertRaises(ValueError):Search("PL","TH",**kw)
    def test_no_cross_currency_results(self):
        with self.assertRaises(ProviderError):parse_results(RT,expected_currency="USD")
    def test_currency_cache_separate(self):
        p=Flightlist();calls=[]
        def fake(url,accept="text/html",currency="USD"):
            calls.append(currency);return RT if currency=="PLN" else OW
        p.get=fake
        p.search(Search("PL","TH"));p.search(Search("PL","TH",currency="PLN"));p.search(Search("PL","TH"))
        self.assertEqual(calls,["USD","PLN"])
    def test_preference_cookie_only(self):
        class Response:
            url=BASE
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,n):return OW.encode()
        with patch("flightbot.urlopen",return_value=Response()) as call:
            Flightlist().get(BASE,currency="PLN")
        self.assertEqual(call.call_args[0][0].get_header("Cookie"),"currency=PLN")
    def test_legacy_monitor_not_duplicated(self):
        s=Store(":memory:");spec=Search("PL","TH",30,"500",1)
        legacy={k:v for k,v in asdict(spec).items() if k not in ("currency","nights_from","nights_to")}
        with s.db:s.db.execute("INSERT INTO watches(owner,spec) VALUES(?,?)",("1",json.dumps(legacy,sort_keys=True)))
        self.assertEqual(s.add("1",spec,parse_results(OW)),1)
        self.assertEqual(len(s.watches("1")),1)
    def test_readable_list(self):
        s=Store(":memory:");s.add("1",Search("PL","TH",currency="PLN",nights_from=10,nights_to=16),parse_results(RT))
        reply=Bot(None,s).handle("1","/lista")
        self.assertIn("10–16 nocy",reply);self.assertNotIn('"currency"',reply)
    def test_roundtrip_alert_currency(self):
        s=Store(":memory:");r=parse_results(RT);s.add("1",Search("PL","TH",currency="PLN"),r)
        class Fake:
            def search(self,spec):return replace(r,offers=[replace(r.offers[0],price="2000")])
        s.scan(Fake());msg=s.db.execute("SELECT body FROM outbox").fetchone()[0]
        self.assertIn("2000 PLN",msg);self.assertNotIn("2000 USD",msg)

if __name__=="__main__":unittest.main()
