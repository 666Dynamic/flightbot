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
