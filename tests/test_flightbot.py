import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import date
from dataclasses import replace
from decimal import Decimal
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flightbot import *
FIXTURE=Path(__file__).with_name("results.html").read_text()

class Fake:
    def __init__(self,result):self.result=result
    def search(self,spec):
        if isinstance(self.result,Exception):raise self.result
        return self.result

class Tests(unittest.TestCase):
    def setUp(self):
        self.store=Store(":memory:")
        self.result=parse_results(FIXTURE)
        self.spec=Search("PL","TH",30,"500")
    def test_real_fixture(self):
        self.assertEqual(len(self.result.offers),2)
        self.assertEqual(self.result.offers[0].currency,"USD")
        self.assertIn("Air Arabia",self.result.offers[0].summary)
        self.assertNotIn("booking_token",self.result.offers[0].url)
    def test_fail_closed(self):
        for html in ("<h1>Access denied</h1>",'<div data-search-region="results">Error</div>',FIXTURE.replace("2 flights found","3 flights found")):
            with self.assertRaises(ProviderError):parse_results(html)
    def test_zero(self):
        self.assertEqual(parse_results('<div data-search-region="results"><h2>0 flights found</h2></div>').offers,[])
    def test_currency_mismatch(self):
        with self.assertRaises(ProviderError):parse_results(FIXTURE.replace("US$","€"),expected_currency="USD")
    def test_price_not_identity(self):
        other=parse_results(FIXTURE.replace(">406<",">399<"))
        self.assertEqual(self.result.offers[0].key,other.offers[0].key)
        self.assertEqual(other.offers[0].price,"399")
    def test_rolling_window(self):
        p=self.spec.params(date(2026,12,15));self.assertEqual(p["depart_to"],"2027-01-13")
        self.assertEqual(p["self_transfer"],0)
    def test_invalid_specs(self):
        for kwargs in ({"days":0},{"days":181},{"stops":5},{"budget":"NaN"},{"budget":"-1"},{"origin":"https://evil.test"},{"start":"2026-10-30"}):
            base={"origin":"PL","destination":"TH"};base.update(kwargs)
            with self.assertRaises((ValueError,ArithmeticError)):Search(**base)
    def test_polish(self):
        action,spec=parse_command("pokaż loty z Polski do Tajlandii w następnych 30 dniach do 500 USD")
        self.assertEqual((action,spec.origin,spec.destination,spec.budget),("search","PL","TH","500"))
    def test_currency_not_silently_converted(self):
        action,spec=parse_command("pokaż loty z Polski do Tajlandii w następnych 30 dniach do 2000 PLN")
        self.assertEqual((spec.budget,spec.currency),("2000","PLN"))
    def test_watch_baseline_and_duplicate(self):
        a=self.store.add("1",self.spec,self.result)
        self.assertEqual(a,self.store.add("1",self.spec,self.result))
        self.store.scan(Fake(self.result))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM outbox").fetchone()[0],0)
    def test_drop_only_once(self):
        self.store.add("1",self.spec,self.result)
        changed=replace(self.result,offers=[replace(self.result.offers[0],price="300")])
        self.store.scan(Fake(changed));self.store.scan(Fake(changed))
        rows=self.store.db.execute("SELECT body FROM outbox").fetchall()
        self.assertEqual(len(rows),1);self.assertIn("Spadek",rows[0][0])
    def test_failure_preserves_state(self):
        self.store.add("1",self.spec,self.result)
        before=self.store.db.execute("SELECT data FROM snapshots").fetchone()[0]
        self.assertEqual(len(self.store.scan(Fake(ProviderError("HTTP 429")))),1)
        self.assertEqual(before,self.store.db.execute("SELECT data FROM snapshots").fetchone()[0])
    def test_empty_does_not_generate_disappearance(self):
        self.store.add("1",self.spec,self.result)
        self.store.scan(Fake(replace(self.result,offers=[],count=0)))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM outbox").fetchone()[0],0)
    def test_ownership(self):
        wid=self.store.add("alice",self.spec,self.result)
        self.assertFalse(self.store.delete("bob",wid));self.assertEqual(self.store.watches("bob"),[])
        self.assertTrue(self.store.delete("alice",wid))
    def test_budget(self):
        self.store.add("1",Search("PL","TH",30,"100"),self.result)
        self.store.scan(Fake(replace(self.result,offers=[replace(self.result.offers[0],key="new",price="200")])))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM outbox").fetchone()[0],0)
    def test_queue_survives_failed_delivery(self):
        with self.store.db:self.store.enqueue("1","test")
        class Broken:
            def send(self,*args):raise RuntimeError("no network")
        with self.assertRaises(RuntimeError):flush(self.store,Broken())
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM outbox").fetchone()[0],1)
    def test_help_offline(self):
        self.assertIn("/szukaj",Bot(Fake(ProviderError("offline")),self.store).handle("1","/help"))
    def test_no_unknown_currency_and_html(self):
        self.assertIn("Nie udało",Bot(Fake(self.result),self.store).handle("1","/szukaj PL TH 30 nope"))
    def test_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/"db")
            s=Store(path);s.add("1",self.spec,self.result);s.db.close()
            s=Store(path);self.assertEqual(len(s.watches("1")),1);s.db.close()

if __name__=="__main__":unittest.main()
