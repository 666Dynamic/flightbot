import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flightbot import *
from connect import save_config,load_config,verify,pair,pairing_match

class FakeTelegram:
    def __init__(self,updates=None,webhook=""):
        self.updates=updates or [];self.calls=[];self.sent=[];self.webhook=webhook
    def call(self,method,payload):
        self.calls.append((method,payload))
        if method=="getMe":return {"id":123,"is_bot":True,"username":"test_flight_bot"}
        if method=="getWebhookInfo":return {"url":self.webhook}
        if method=="getUpdates":return self.updates
        raise AssertionError(method)
    def send(self,owner,body):self.sent.append((owner,body))

class Provider:
    def search(self,spec):
        return parse_results(Path(__file__).with_name("results.html").read_text())

def update(uid=1,owner=42,kind="private",text="/help"):
    return {"update_id":uid,"message":{"chat":{"id":owner,"type":kind},"from":{"id":owner,"is_bot":False},"text":text}}

class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(":memory:");self.bot=Bot(Provider(),self.store)
    def test_authorized_reply_queue_then_delivery(self):
        off=process_updates(self.bot,[update()],{"42"},0)
        self.assertEqual(off,2)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM outbox").fetchone()[0],1)
        t=FakeTelegram();flush(self.store,t)
        self.assertEqual(t.sent[0][0],"42");self.assertIn("/szukaj",t.sent[0][1])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM outbox").fetchone()[0],0)
    def test_unauthorized_chat_and_groups_ignored(self):
        process_updates(self.bot,[update(1,43),update(2,42,"group")],{"42"},0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM outbox").fetchone()[0],0)
    def test_duplicate_update_not_processed_twice(self):
        off=process_updates(self.bot,[update()],{"42"},0)
        process_updates(self.bot,[update()],{"42"},off)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM outbox").fetchone()[0],1)
    def test_complete_local_conversation_and_alert(self):
        off=process_updates(self.bot,[update(text="/szukaj PL TH 30 500 1"),update(2,text="/obserwuj PL TH 30 500 1")],{"42"},0)
        self.assertEqual(len(self.store.watches("42")),1)
        result=Provider().search(None)
        from dataclasses import replace
        class Cheaper:
            def search(self,spec):return replace(result,offers=[replace(result.offers[0],price="200")])
        self.assertEqual(self.store.scan(Cheaper()),[])
        t=FakeTelegram();flush(self.store,t)
        self.assertEqual(len(t.sent),3);self.assertIn("Spadek",t.sent[-1][1])
        process_updates(self.bot,[update(3,text="/usun 1")],{"42"},off)
        self.assertEqual(self.store.watches("42"),[])
    def test_pairing_requires_code_private_and_same_sender(self):
        u=update(text="/start abc")
        self.assertTrue(pairing_match(u,"abc"))
        self.assertFalse(pairing_match(u,"other"))
        u["message"]["chat"]["type"]="group";self.assertFalse(pairing_match(u,"abc"))
        u["message"]["chat"]["type"]="private";u["message"]["from"]["id"]=999
        self.assertFalse(pairing_match(u,"abc"))
    def test_pairing_reads_no_send(self):
        t=FakeTelegram([update(3,99,text="/start wrong"),update(4,42,text="/start abc")])
        self.assertEqual(pair(t,"abc"),("42",5));self.assertEqual(t.sent,[])
    def test_existing_webhook_rejected_without_mutation(self):
        t=FakeTelegram(webhook="https://example.org/hook")
        with self.assertRaises(RuntimeError):verify(t)
        self.assertEqual([c[0] for c in t.calls],["getMe","getWebhookInfo"])
    def test_changed_bot_identity_rejected(self):
        with self.assertRaises(RuntimeError):verify(FakeTelegram(),999)
    def test_config_permissions_and_loading(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"runtime"/"telegram.json"
            data={"token":"123:"+"a"*30,"chat_id":"42"}
            save_config(p,data)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600)
            self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode),0o700)
            self.assertEqual(load_config(p),data)
            os.chmod(p,0o644)
            with self.assertRaises(RuntimeError):load_config(p)
    def test_telegram_transport_redacts_exception(self):
        with patch("flightbot.urlopen",side_effect=OSError("https://secret-token")):
            with self.assertRaises(RuntimeError) as error:Telegram("secret-token").call("getMe",{})
        self.assertNotIn("secret-token",str(error.exception))
    def test_reply_contains_no_html_parse_mode(self):
        t=Telegram("fake");calls=[]
        t.call=lambda m,p:calls.append((m,p))
        t.send("42","<b>literal</b>")
        self.assertNotIn("parse_mode",calls[0][1])

if __name__=="__main__":unittest.main()
