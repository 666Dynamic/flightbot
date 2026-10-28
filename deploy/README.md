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
