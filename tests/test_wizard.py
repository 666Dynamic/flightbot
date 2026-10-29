import sys
import unittest
from datetime import date
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flightbot import *
HTML=Path(__file__).with_name("results.html").read_text()

class Provider:
    def __init__(self):self.specs=[];self.place_labels={}
    def search(self,spec,refresh=False):
        self.specs.append(spec);return parse_results(HTML,BASE+"?from=PL&to=TH")
    def locations(self,term):
        data={"Rome":[{"value":"city:ROM","text":"Rome (ROM), Italy","type":"city"}],
              "santiago":[{"value":"city:SCL","text":"Santiago (SCL), Chile","type":"city"},
                          {"value":"city:STI","text":"Santiago (STI), Dominican Republic","type":"city"}]}
        values=data.get(term,[]);self.place_labels.update({v["value"]:v["text"] for v in values});return values

class FakeTelegram:
    def __init__(self,edit_error=None):self.calls=[];self.edit_error=edit_error
    def edit(self,owner,mid,text):
        self.calls.append(("edit",mid,str(text)))
        if self.edit_error:raise TelegramRejected(self.edit_error)
    def send(self,owner,text):self.calls.append(("send",None,str(text)))

def callbacks(reply):
    return [re.sub(r"^wz:[a-f0-9]{12}:","wz:",b.get("callback_data", "")) for row in reply.markup["inline_keyboard"] for b in row]

class WizardTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(":memory:");self.p=Provider();self.b=Bot(self.p,self.s)
    def click(self,data):
        state=self.s.wizard_get("42")
        if state and data.startswith("wz:"):data="wz:"+state["token"]+":"+data[3:]
        return self.b.callback("42",data)
    def full_oneway(self):
        self.b.handle("42","/kreator")
        for data in ("wz:t:oneway","wz:o:city:WAW","wz:d:ES","wz:w:30","wz:s:1","wz:b:1000"):last=self.click(data)
        return last
    def test_start_shows_trip_step(self):
        reply=self.b.handle("42","/kreator")
        self.assertIn("Krok 1",reply);self.assertIn("wz:t:oneway",callbacks(reply));self.assertFalse(reply.edit)
    def test_all_callback_data_fits_telegram_limit(self):
        self.b.handle("42","/kreator")
        for step in WIZARD_STEPS[:-1]:
            data=self.s.wizard_get("42");data.update(step=step,trip="oneway",origin="PL",destination="ES");self.s.wizard_set("42",data)
            reply=self.b.wizard_prompt(data)
            self.assertTrue(all(len(c.encode())<=64 for c in callbacks(reply) if c))
    def test_oneway_flow_reaches_summary_and_search(self):
        reply=self.full_oneway()
        self.assertIn("Podsumowanie",reply);self.assertIn("Warszawa → Hiszpania",reply);self.assertTrue(reply.edit)
        self.assertIn("do 1000 PLN",reply);self.assertIn("maks. 1 przesiadka",reply)
        result=self.click("wz:go");spec=self.p.specs[-1]
        self.assertEqual((spec.origin,spec.destination,spec.days,spec.stops,spec.budget,spec.currency,spec.nights_from),
                         ("city:WAW","ES",30,1,"1000","PLN",0))
        self.assertIn("wz:edit",callbacks(result));self.assertTrue(any(c.startswith("watch:") for c in callbacks(result) if c))
    def test_return_trip_asks_nights(self):
        self.b.handle("42","/kreator")
        for data in ("wz:t:return","wz:o:PL","wz:d:TH"):self.click(data)
        reply=self.click("wz:w:next");self.assertIn("Ile nocy",reply)
        reply=self.click("wz:n:10-14");self.assertIn("Krok 6/7",reply)
        data=self.s.wizard_get("42");self.assertEqual((data["nights_from"],data["nights_to"]),(10,14))
        self.assertTrue(data["start"].endswith("-01"))
    def test_back_skips_nights_for_oneway(self):
        self.b.handle("42","/kreator")
        for data in ("wz:t:oneway","wz:o:PL","wz:d:TH","wz:w:30"):self.click(data)
        self.assertIn("Krok 4/6 · Kiedy",self.click("wz:back"))
    def test_typed_place_and_ambiguous_choice(self):
        self.b.handle("42","/kreator");self.click("wz:t:oneway");self.click("wz:custom")
        reply=self.b.handle("42","Rzym");self.assertIn("Krok 3/6",reply)
        self.assertEqual(self.s.wizard_get("42")["origin"],"city:ROM")
        self.click("wz:custom");reply=self.b.handle("42","santiago");self.assertIn("wz:pick:1",callbacks(reply))
        reply=self.click("wz:pick:1");self.assertIn("Krok 4/6",reply)
        self.assertEqual(self.s.wizard_get("42")["destination_name"],"Santiago (STI), Dominican Republic")
    def test_typed_dates_nights_budget(self):
        self.b.handle("42","/kreator")
        for data in ("wz:t:return","wz:o:PL","wz:d:TH"):self.click(data)
        year=datetime.now(TZ).year+1
        self.click("wz:custom");self.b.handle("42",f"10.01.{year}-20.01.{year}")
        data=self.s.wizard_get("42");self.assertEqual((data["start"],data["end"]),(f"{year}-01-10",f"{year}-01-20"))
        self.click("wz:custom");self.b.handle("42","6-9");self.click("wz:s:4");self.click("wz:custom")
        reply=self.b.handle("42","1 500 zł");self.assertIn("do 1500 PLN",reply)
    def test_invalid_text_keeps_step_with_warning(self):
        self.b.handle("42","/kreator")
        for data in ("wz:t:return","wz:o:PL","wz:d:TH"):self.click(data)
        self.click("wz:custom");reply=self.b.handle("42","kiedyś");self.assertIn("⚠️",reply);self.assertIn("Wpisz tylko daty",reply)
        reply=self.b.handle("42","01.01.2020-05.01.2020");self.assertIn("przeszłości",reply)
    def test_edit_from_summary_returns_to_summary(self):
        self.full_oneway()
        self.assertIn("Krok 6/6",self.click("wz:ed:budget"))
        self.assertIn("Podsumowanie",self.click("wz:b:0"))
        self.assertEqual(self.s.wizard_get("42")["budget"],"")
        self.click("wz:ed:trip");self.assertIn("Ile nocy",self.click("wz:t:return"))
        self.assertIn("Podsumowanie",self.click("wz:n:3-5"))
    def test_sentence_bypasses_wizard(self):
        self.b.handle("42","/kreator");self.assertTrue(looks_like_sentence("loty z Warszawy do Rzymu"))
        self.assertFalse(looks_like_sentence("Rzym"));self.assertFalse(looks_like_sentence("Nowy Jork"))
    def test_other_owner_has_no_wizard(self):
        self.b.handle("42","/kreator");reply=self.b.callback("43","wz:o:PL")
        self.assertIn("wygasł",reply);self.assertIsNone(self.s.wizard_get("43"))
    def test_cancel_and_expiry(self):
        self.b.handle("42","/kreator");self.assertIn("Anulowano",self.click("wz:x"))
        self.assertIsNone(self.s.wizard_get("42"))
        self.b.handle("42","/kreator")
        with self.s.db:self.s.db.execute("UPDATE wizards SET expires=0")
        self.assertIn("wygasł",self.click("wz:d:ES"))
    def test_forged_values_rejected(self):
        self.b.handle("42","/kreator")
        for data in ("wz:o:XX","wz:s:9","wz:zz:1","wz:pick:5","wz:ed:nothing"):
            self.assertIn("⚠️",self.click(data))
        self.assertEqual(self.s.wizard_get("42")["origin"],"")
    def test_polish_country_and_bali_names(self):
        self.b.handle("42","/kreator");self.click("wz:t:oneway");self.click("wz:custom");self.b.handle("42","Włochy")
        self.assertEqual(self.s.wizard_get("42")["origin"],"IT")
        self.click("wz:custom");reply=self.b.handle("42","bali");self.assertIn("✅ Dokąd: Bali",reply)
        self.assertEqual(self.s.wizard_get("42")["destination"],"DPS")
    def test_provider_empty_state(self):
        result=parse_results(Path(__file__).with_name("no-results.html").read_text())
        self.assertEqual((result.count,result.offers),(0,[]))
        self.assertIn("Brak lotów dla tych filtrów",render(result,Search("PL","TH")))
    def test_next_month_and_ranges(self):
        self.assertEqual(next_month(date(2026,12,15)),("2027-01-01","2027-01-31"))
        self.assertEqual(next_month(date(2027,1,31)),("2027-02-01","2027-02-28"))
        self.assertEqual(parse_date_range("20.12.2026-05.01.2027",date(2026,10,28)),("2026-12-20","2027-01-05"))
        with self.assertRaises(ValueError):parse_date_range("01.01.2027-01.12.2027",date(2026,10,28))

class EditQueueTests(unittest.TestCase):
    def setUp(self):self.s=Store(":memory:")
    def queue(self,edit_id):
        reply=Reply("tekst",keyboard([button("x","wz:x")]));reply.edit_id=edit_id
        with self.s.db:self.s.enqueue("42",reply)
    def test_callback_reply_edits_message(self):
        bot=Bot(Provider(),self.s)
        update={"update_id":5,"callback_query":{"id":"q","data":"wz:new","from":{"id":42},
                "message":{"message_id":77,"chat":{"id":42,"type":"private"}}}}
        process_updates(bot,[update],{"42"},0)
        self.assertEqual(self.s.db.execute("SELECT edit_id FROM outbox").fetchone()[0],None)
        bot.store.wizard_set("42",wizard_new())
        update["update_id"]=6;update["callback_query"]["data"]="wz:"+bot.store.wizard_get("42")["token"]+":t:oneway"
        process_updates(bot,[update],{"42"},0)
        self.assertEqual(self.s.db.execute("SELECT edit_id FROM outbox ORDER BY id DESC").fetchone()[0],77)
    def test_flush_edit_and_fallbacks(self):
        t=FakeTelegram();self.queue(77);flush(self.s,t);self.assertEqual(t.calls[0][:2],("edit",77))
        t=FakeTelegram("Bad Request: message is not modified");self.queue(77);flush(self.s,t)
        self.assertEqual([c[0] for c in t.calls],["edit"])
        t=FakeTelegram("Bad Request: message to edit not found");self.queue(77);flush(self.s,t)
        self.assertEqual([c[0] for c in t.calls],["edit","send"])
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM outbox").fetchone()[0],0)

if __name__=="__main__":unittest.main()
