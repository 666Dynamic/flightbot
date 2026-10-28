import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flightbot import *
HTML=Path(__file__).with_name("results.html").read_text()

class Provider:
    def __init__(self):self.calls=[]
    def search(self,spec,refresh=False):
        self.calls.append(refresh)
        return parse_results(HTML,BASE+"?from=PL&to=TH")

class ButtonTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(":memory:");self.p=Provider();self.b=Bot(self.p,self.s)
    def search_reply(self):return self.b.handle("42","/szukaj PL TH 30 500 1")
    def test_button_and_opaque_short_data(self):
        reply=self.search_reply();data=reply.markup["inline_keyboard"][0][0]["callback_data"]
        self.assertTrue(data.startswith("watch:"));self.assertLessEqual(len(data.encode()),64)
        self.assertNotIn(BASE,reply)
    def test_click_watch_idempotent(self):
        data=self.search_reply().markup["inline_keyboard"][0][0]["callback_data"]
        self.b.callback("42",data);self.b.callback("42",data)
        self.assertEqual(len(self.s.watches("42")),1)
    def test_foreign_search_button_denied(self):
        data=self.search_reply().markup["inline_keyboard"][0][0]["callback_data"]
        self.b.callback("43",data);self.assertEqual(self.s.watches("43"),[])
    def test_expired_button_denied(self):
        data=self.search_reply().markup["inline_keyboard"][0][0]["callback_data"]
        with self.s.db:self.s.db.execute("UPDATE actions SET expires=0")
        self.b.callback("42",data);self.assertEqual(self.s.watches("42"),[])
    def add_watch(self):return self.s.add("42",Search("PL","TH"),self.p.search(None))
    def test_manual_refresh(self):
        wid=self.add_watch();reply=self.b.callback("42",f"check:{wid}")
        self.assertTrue(self.p.calls[-1]);self.assertIn("na żądanie",reply)
        self.assertNotIn(BASE,reply);self.assertIn("url",reply.markup["inline_keyboard"][1][0])
    def test_delete_and_restore(self):
        wid=self.add_watch();reply=self.b.callback("42",f"delete:{wid}")
        self.assertEqual(self.s.watches("42"),[])
        self.assertTrue(reply.markup["inline_keyboard"][0][0]["callback_data"].startswith("restore:"))
        self.b.callback("42",f"restore:{wid}");self.assertEqual(len(self.s.watches("42")),1)
    def test_foreign_monitor_actions_denied(self):
        wid=self.add_watch()
        for verb in ("check","delete","restore"):self.b.callback("99",f"{verb}:{wid}")
        self.assertEqual(len(self.s.watches("42")),1)
    def test_deleted_monitor_not_scanned(self):
        wid=self.add_watch();self.s.delete("42",wid);before=len(self.p.calls)
        self.s.scan(self.p);self.assertEqual(len(self.p.calls),before)
    def test_reobserve_restores_same_id(self):
        wid=self.add_watch();self.s.delete("42",wid)
        self.assertEqual(self.add_watch(),wid)
        self.assertEqual(len(self.s.watches("42")),1)
    def test_markup_persisted_with_outbox(self):
        reply=self.search_reply()
        with self.s.db:self.s.enqueue("42",reply)
        class T:
            def send(inner,owner,text):self.assertEqual(text.markup,reply.markup)
        flush(self.s,T());self.assertEqual(self.s.db.execute("SELECT count(*) FROM outbox").fetchone()[0],0)
    def test_callback_update_checks_sender_and_acknowledges(self):
        data=self.search_reply().markup["inline_keyboard"][0][0]["callback_data"]
        u={"update_id":1,"callback_query":{"id":"cb1","from":{"id":42},"data":data,"message":{"chat":{"id":42,"type":"private"}}}}
        calls=[]
        class T:
            def call(inner,m,p):calls.append((m,p))
        offset=process_updates(self.b,[u],{"42"},0,T())
        self.assertEqual(offset,2);self.assertEqual(calls[0][0],"answerCallbackQuery")
        self.assertEqual(len(self.s.watches("42")),1)
        process_updates(self.b,[u],{"42"},offset,T());self.assertEqual(len(calls),1)
    def test_forged_sender_rejected(self):
        wid=self.add_watch()
        u={"update_id":1,"callback_query":{"id":"cb1","from":{"id":99},"data":f"delete:{wid}","message":{"chat":{"id":42,"type":"private"}}}}
        process_updates(self.b,[u],{"42"},0)
        self.assertEqual(len(self.s.watches("42")),1)
    def test_action_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(str(Path(d)/"db"));tok=s.action("42",Search("PL","TH"));s.db.close()
            s=Store(str(Path(d)/"db"));self.assertEqual(s.action_spec("42",tok).destination,"TH")
    def test_legacy_outbox_migrated(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/"db");db=sqlite3.connect(path)
            db.execute("CREATE TABLE outbox(id INTEGER PRIMARY KEY,owner TEXT NOT NULL,body TEXT NOT NULL)")
            db.execute("INSERT INTO outbox(owner,body) VALUES('42','old reply')");db.commit();db.close()
            s=Store(path);row=s.db.execute("SELECT * FROM outbox").fetchone()
            self.assertEqual(row["body"],"old reply");self.assertIsNone(row["markup"])
    def test_telegram_receives_markup(self):
        calls=[];t=Telegram("fake");t.call=lambda m,p:calls.append(p)
        r=self.search_reply();t.send("42",r)
        self.assertEqual(calls[0]["reply_markup"],r.markup)

if __name__=="__main__":unittest.main()
