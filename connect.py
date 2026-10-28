#!/usr/bin/env python3
"""Local Telegram setup; credentials are entered with hidden getpass input."""
import argparse
import fcntl
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import time
from flightbot import Bot, Flightlist, Store, Telegram, serve

ROOT=Path(__file__).resolve().parent
# Keep secrets and runtime state outside the deliverable folder and ZIP.
# FLIGHTBOT_STATE overrides the location on a server (e.g. systemd unit).
STATE=Path(os.environ.get("FLIGHTBOT_STATE") or ROOT.parent.parent/"work"/"flightbot-runtime")
CONFIG=STATE/"telegram.json"


def save_config(path,data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.parent.is_symlink() or path.is_symlink():
        raise RuntimeError("Ścieżka konfiguracji nie może być dowiązaniem.")
    os.chmod(path.parent,0o700)
    fd,temp=tempfile.mkstemp(prefix=".telegram-",dir=path.parent)
    try:
        with os.fdopen(fd,"w") as f:
            os.fchmod(f.fileno(),0o600)
            json.dump(data,f)
            f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def load_config(path=CONFIG):
    path=Path(path)
    if path.is_symlink():raise RuntimeError("Konfiguracja jest dowiązaniem.")
    if stat.S_IMODE(path.stat().st_mode)&0o077:
        raise RuntimeError("Konfiguracja musi mieć uprawnienia 600.")
    with path.open() as f:data=json.load(f)
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}",data.get("token","")):
        raise RuntimeError("Nieprawidłowy format tokenu w konfiguracji.")
    if not re.fullmatch(r"[1-9]\d*",str(data.get("chat_id",""))):
        raise RuntimeError("Nieprawidłowy identyfikator prywatnego czatu.")
    return data


def verify(telegram,expected_id=None):
    me=telegram.call("getMe",{})
    if not me.get("is_bot") or not me.get("username"):
        raise RuntimeError("Token nie wskazuje bota Telegram.")
    if expected_id is not None and me["id"]!=expected_id:
        raise RuntimeError("Tożsamość bota różni się od zapisanej konfiguracji.")
    webhook=telegram.call("getWebhookInfo",{})
    if webhook.get("url"):
        raise RuntimeError("Ten bot ma aktywny webhook. Wybierz osobnego testowego bota; niczego nie przełączono.")
    return me


def pairing_match(update,code):
    msg=update.get("message",{})
    chat=msg.get("chat",{})
    sender=msg.get("from",{})
    return (chat.get("type")=="private" and isinstance(chat.get("id"),int)
            and chat["id"]>0 and sender.get("id")==chat["id"]
            and not sender.get("is_bot",False)
            and secrets.compare_digest(msg.get("text","").strip(),"/start "+code))


def pair(telegram,code,timeout=300):
    deadline=time.monotonic()+timeout
    offset=0
    while time.monotonic()<deadline:
        updates=telegram.call("getUpdates",{"offset":offset,"timeout":15,"allowed_updates":["message"]})
        for update in updates:
            if pairing_match(update,code):
                return str(update["message"]["chat"]["id"]),update["update_id"]+1
            offset=max(offset,update["update_id"]+1)
    raise RuntimeError("Minęło 5 minut. Uruchom konfigurator ponownie, aby wygenerować nowy kod.")


def configure():
    if CONFIG.exists():
        raise RuntimeError("Konfiguracja już istnieje. Użyj check lub run. Aby zmienić bota, przenieś najpierw telegram.json do bezpiecznej kopii.")
    print("Utwórz osobnego bota: https://t.me/BotFather → /newbot.")
    print("Wklej jego token poniżej. Wpis nie będzie widoczny ani zapisany w historii poleceń.")
    token=getpass.getpass("Token testowego bota: ").strip()
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}",token):
        raise RuntimeError("Token ma nieprawidłowy format.")
    telegram=Telegram(token)
    me=verify(telegram)
    code=secrets.token_hex(8)
    print(f"Zweryfikowano @{me['username']}.")
    print("Otwórz w Telegramie TEN link i naciśnij Start:")
    print(f"https://t.me/{me['username']}?start={code}")
    print(f"Możesz też wysłać do tego bota dokładnie: /start {code}")
    print("Czekam maksymalnie 5 minut; parowanie nie wysyła wiadomości.",flush=True)
    chat_id,offset=pair(telegram,code)
    save_config(CONFIG,{"token":token,"bot_id":me["id"],"username":me["username"],"chat_id":chat_id,"offset":offset})
    print("Połączono prywatny czat. Token zapisano lokalnie z uprawnieniami 600.")
    print("Uruchom: python3 connect.py run")


def run(interval):
    data=load_config()
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(STATE,0o700)
    # Prevent two polling clients sharing this configuration/database.
    with (STATE/"bot.lock").open("w") as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError("Bot jest już uruchomiony dla tej konfiguracji.") from None
        telegram=Telegram(data["token"])
        verify(telegram,data["bot_id"])
        store=Store(str(STATE/"state.sqlite3"))
        os.chmod(STATE/"state.sqlite3",0o600)
        with store.db:
            store.db.execute("INSERT OR IGNORE INTO meta VALUES('offset',?)",(str(data["offset"]),))
        print(f"Bot @{data['username']} działa. W Telegramie wyślij /help. Ctrl+C zatrzymuje proces.",flush=True)
        try:serve(Bot(Flightlist(),store),telegram,{data["chat_id"]},interval)
        finally:store.db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=["configure","check","run"])
    parser.add_argument("--interval",type=int,default=3600)
    args=parser.parse_args()
    if args.interval<900:parser.error("Minimalny odstęp to 900 sekund.")
    try:
        if args.mode=="configure":configure()
        elif args.mode=="check":
            data=load_config();me=verify(Telegram(data["token"]),data["bot_id"])
            print(f"OK: @{me['username']}, prywatny czat skonfigurowany, brak webhooka. Nie wysłano wiadomości.")
        else:run(args.interval)
    except FileNotFoundError:
        parser.exit(1,"Brak konfiguracji. Uruchom najpierw: python3 connect.py configure\n")
    except KeyboardInterrupt:
        print("\nZatrzymano.")
    except (RuntimeError,ValueError,OSError):
        # Avoid ever printing arbitrary exception strings/URLs with credentials.
        parser.exit(1,"Nie udało się połączyć. Sprawdź token, połączenie, webhook i plik konfiguracji (uprawnienia 600). Konfiguracja może już istnieć lub bot może być uruchomiony.\n")

if __name__=="__main__":main()
