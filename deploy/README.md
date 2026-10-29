# Wdrożenie na serwer (Ubuntu, systemd)

Działa na Oracle `chmura` (Tailscale 100.104.117.37) od 28.10.2026.

- Kod: `/opt/flightbot/app` (root, 644)
- Stan i sekrety: `/opt/flightbot/state` (użytkownik `flightbot`, 700/600): `telegram.json`, `state.sqlite3`
- Usługa: `flightbot.service` (ten katalog), `Restart=always`, start z systemem

Sekretów NIE ma w repo. `telegram.json` i bazę trzeba przenieść osobno.

## Aktualizacja kodu

```sh
scp flightbot.py connect.py chmura:/tmp/
ssh chmura 'sudo install -m 644 /tmp/flightbot.py /tmp/connect.py /opt/flightbot/app/ && sudo systemctl restart flightbot'
```

## Podgląd i kopia bazy

```sh
ssh chmura 'systemctl status flightbot; journalctl -u flightbot -n 50'
ssh chmura 'sudo -u flightbot sqlite3 /opt/flightbot/state/state.sqlite3 ".backup /tmp/flightbot-backup.sqlite3"'
```

## Przeniesienie na inny serwer

1. Skopiuj `telegram.json` i `state.sqlite3` do `/opt/flightbot/state` (właściciel `flightbot`, 600).
2. Skopiuj kod do `/opt/flightbot/app` i `flightbot.service` do `/etc/systemd/system/`.
3. Zatrzymaj starą instancję, potem `sudo systemctl enable --now flightbot`. Nigdy dwie naraz.

## Wdrożenie kreatora 29.10.2026

Kod `4fec578` wdrożono do `/opt/flightbot/app/flightbot.py`; SHA256 `5baa9cb42740d1ea8c07a927fd610857e26a18e7f7b4e0c8f115a964312cb437`. Systemd po restarcie: active. Kopia kodu i SQLite: `/opt/flightbot/backups/button-wizard-20261029T122421Z` (root, katalog700, baza600). Stan obserwacji zachowany: #1 wyłączona, #2 wyłączona, #3 aktywna.

Samo `ssh chmura` nie korzysta tu z konfiguracji. Działająca konfiguracja na Macu:
`/Users/lukas/Documents/Codex/2026-10-25/referenced-chatgpt-conversation-this-is-an/work/ssh-cloud.conf`
Użycie: `rtk proxy ssh -F /pełna/ścieżka/ssh-cloud.conf chmura ...`. Plik wskazuje użytkownika ubuntu, prywatny adres Tailscale, plik klucza i właściwy known_hosts; weryfikacja klucza pozostaje włączona. Nie kopiować sekretów do repo.

Log produkcji potwierdził powtarzające się HTTP403 wyszukiwarki Flightlist od 28.10. Formularz po błędzie zachowuje filtry i udostępnia bezpośredni link. Działanie usługi nie jest równoznaczne z dostępnością ofert.

## Działające źródło Kiwi — 29.10.2026

Kod wykonawczy 3ee32e3 (flightbot.py, connect.py, kiwi_provider.py) wdrożono z backupem `/opt/flightbot/backups/kiwi-20261029T125716Z`. systemd active, baza integrity_check=ok; dodano snapshots.source. Nie przenoszono sekretów ani nie zmieniano stanów monitorów (#1off,#2off,#3on). W razie wycofania starszy kod nie obsługuje dodatkowej kolumny snapshotów: wycofanie wymaga zgodnej migracji albo odtworzenia bazy przy zatrzymanej usłudze, z uwzględnieniem zmian po backupie.

Nowy moduł kiwi_provider.py MUSI być instalowany razem z flightbot.py i connect.py. Samo skopiowanie dwóch plików jak w starym przykładzie nie wystarczy.

Żywe testy adaptera z Oracle: PL→PH15ofert/1430PLN, GDN→HAM bezpośrednio15ofert/154PLN, WAW→MNL powrót10–14nocy15ofert/2733PLN. Ceny wyłącznie z czasu testu. W Telegramie przez Safari potwierdzono wyszukiwanie zachowanych filtrów PL→PT7dni/max1stop: bot pokazał5ofert z linkami, od165PLN. Nie uruchamiano dodatkowego pollera ani GitHub Actions. Source change ustawia baseline bez fałszywych alertów.

Dodatkowy test Telegrama: pełna podróż powrotna Warszawa→Filipiny10–14nocy do5000PLN zwróciła oferty2733PLN. Ręczne sprawdzenie aktywnego monitora#3 zwróciło oferty1430PLN. Produkcyjny scan bez wysyłania: errors=[], outbox0→0; snapshot#3 ma source=Kiwi.com, stan aktywnych monitorów bez zmian.

## Uproszczony kreator: 72299a5, 29.10.2026

Wdrożono flightbot.py: skrócone listy bez stronicowania, PLN/EUR/USD w budżecie, obsługa polskich nazw krajów, poprawione cofanie i przerwanie edycji. Backup poprzedniego modułu: /opt/flightbot/backups/kiwi-20261029T125716Z/flightbot-before-simple-wizard.py. SHA256 wdrożonego flightbot.py: 86acfe2e946da487b63038811c22dbdf9daab4fed9074a66907c88708889083a (identyczny z lokalnym). Service active. 148 testów offline; GitHub Actions Python 3.9 i 3.12 zakończone sukcesem. W Safari/Telegram potwierdzono cofnięcie z miejsca wylotu do typu podróży, skrócone listy, wybór EUR i rzeczywiste oferty PL→PH/14 dni/maks.2/do500EUR (pierwsza 377EUR w chwili testu). Dowody w folderze nadrzędnym outputs: telegram-currency-wizard-crop.png i telegram-eur-results.png. Nie tworzono nowych obserwacji podczas testów.
