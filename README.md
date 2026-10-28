# Flightbot — działająca wersja lokalna

Bot Telegram z wyszukiwaniem i obserwacjami Flightlist. Python 3.9+, biblioteka standardowa. Kod starego monitora Rainbow nie został zmieniony.

## Co działa

- Kraje, lotniska i identyfikatory regionów z podpowiedzi Flightlist.
- Loty w jedną stronę oraz tam i z powrotem z zakresem pobytu w nocach.
- Ceny USD, PLN i EUR otrzymywane od Flightlist; bez własnego przeliczania kursów.
- Monitorowanie nowych ofert i spadków cen, baza SQLite, kolejka odpowiedzi, izolacja dozwolonych prywatnych czatów.
- Daty w formacie dd.mm.rrrr, godziny 24h i czytelna lista monitorów.
- Wyszukiwanie zwykłymi zdaniami po polsku, z rozpoznawaniem miejsc i przyciskami doprecyzowania; to parser wzorców, nie pełny asystent AI.

## Kreator krok po kroku

`/kreator` (też w menu bota i pod `/help`) prowadzi przyciskami: skąd → dokąd → kiedy → jedna strona / tam i z powrotem (+ liczba nocy) → przesiadki → budżet w PLN → podsumowanie. Na każdym kroku można zamiast przycisku wpisać własną wartość: miasto, kraj lub region (np. „Bali”, „Skandynawia”), daty `10.12-20.12`, pobyt `6-9`, kwotę `1500`. Kreator edytuje jedną wiadomość zamiast wysyłać nowe. W podsumowaniu każdy filtr można zmienić osobno; pod wynikami jest „🔁 Zmień filtry”. Stan kreatora trzymany jest 24 h w tabeli `wizards`. Zwykłe zdania („loty z Warszawy do Rzymu”) działają także w trakcie kreatora.

## Przyciski

Pod nowymi wynikami są przyciski **Obserwuj te loty**, **Wszystkie wyniki Flightlist** i **Moje obserwacje**. Po zapisaniu obserwacji można nacisnąć **Sprawdź teraz**, **Usuń** lub wyświetlić listę. „Sprawdź teraz” pomija cache klienta, ale serwer Flightlist może nadal korzystać z własnej pamięci wyników. Sprawdzenie na żądanie pokazuje wyniki i nie przestawia bazy porównawczej automatycznych alertów.

„Usuń” wyłącza obserwację; przycisk **Przywróć** w odpowiedzi pozwala ją ponownie włączyć. Historia pozostaje w lokalnej bazie. Przywrócenie może wykazać zmiany z okresu wyłączenia podczas kolejnego sprawdzenia. Wyszukania są przypisane do czatu; przyciski „Obserwuj” wygasają po 7 dniach i przetrwają restart. Przyciski pojawiają się w nowych odpowiedziach; starsze wiadomości pozostają bez zmian.

## Przykłady na Telegramie

```text
/szukaj pl de
/szukaj PL TH 30 500 1
/szukaj PL TH 30 4000 1 PLN 10-16
/obserwuj PL TH 30 4000 1 PLN 10-16
/lista
/usun 2
/lokalizacje South East Asia
```

Kolejno: skąd, dokąd, liczba dni do wylotu, budżet, maks. przesiadki, waluta, opcjonalny pobyt min-max nocy. Pominięcie liczby dni oznacza 30; pominięcie waluty oznacza USD. Stare monitory zachowują swoją walutę i budżet. Pobyt oznacza podróż powrotną; budżet dotyczy całej podróży jednej osoby. Bez pobytu wyszukiwana jest jedna strona.

```text
pokaż loty z Polski do Tajlandii w następnych 30 dniach do 4000 PLN, pobyt 10-16 nocy, maksymalnie 1 przesiadka
pokaż loty z Polski do Filipin w następnych 30 dniach do 500 USD
pokaż loty z Polski do Manili w następnych 30 dniach do 500 USD
```

Możesz pisać bez komend i kodów: „wyszukaj mi loty z Polski do Filipin najtańsze”, „z Warszawy do Manili”, „z Gdańska do Hamburga”. Bot rozpoznaje miejsca przez podpowiedzi Flightlist; przy niejednoznacznej nazwie pokazuje przyciski wyboru. Bez dat przyjmuje następne 30 dni, jedną stronę i PLN. Obsługuje też „w przyszłym miesiącu”. To parser wzorców; nierozpoznane filtry są odrzucane, a nie ignorowane.

## Uruchomienie

Dla już skonfigurowanego bota: `python3 connect.py run`. Nie uruchamiaj drugiego procesu równolegle. Lokalny launcher `Połącz Telegram.command` prowadzi konfigurację i start; wpis tokenu jest ukryty, a czat parowany jednorazowym kodem. `python3 connect.py check` sprawdza token i brak webhooka bez wysyłania wiadomości. Konfigurator nie przełącza istniejących webhooków.

Token, identyfikator dozwolonego czatu, baza i log są w `work/flightbot-runtime/` poza pakietem kodu. Uprawnienia folderu: 700; konfiguracji i bazy: 600. Kopia bazy sprzed aktualizacji: `before-v2.sqlite3` w tym samym folderze. Te pliki nie są dołączane do ZIP. `bot.pid` wskazuje uruchomiony proces; launcher korzysta z blokady plikowej.

Usługa działa lokalnie. Uśpienie/wyłączenie komputera przerywa działanie. Nie zainstalowano autostartu ani hostingu całodobowego. Zamknięcie samego Safari nie przerywa pracy procesu uruchomionego w tle.

## Testy bez sieci i Telegrama

Z tego folderu:

```sh
python3 -B -m unittest discover -s tests -v
python3 flightbot.py --db /tmp/flightbot-demo.sqlite3 chat '/szukaj PL TH 30 500 1' --fixture tests/results.html
python3 flightbot.py --db /tmp/flightbot-demo.sqlite3 chat '/szukaj PL TH 30 4000 1 PLN 10-16' --fixture tests/roundtrip-pln.html
```

Fixture odtwarza zapisane ceny; bez `--fixture` wyszukiwanie jest prawdziwym zapytaniem sieciowym. `scan` odpytuje zapisane monitory i zapisuje alerty w kolejce, ale sam ich nie wysyła. Nie mieszaj testowej bazy właściciela `local` z rzeczywistą bazą Telegrama.

## Potwierdzone wykonaniem 27.10.2026

74 testy przechodzą: parser rzeczywistego HTML, waluty i ich rozdzielony cache, loty powrotne, zakres dat przez granicę roku, polecenia, zachowanie starego monitora, własność czatów, kolejka, parowanie i obsługa błędów. Transport Telegram w testach jest zastępowany atrapą.

Niezależnie od testów: gotowy adapter pobrał 100 ofert powrotnych PL → TH z pobytem 10–16 nocy w PLN. Scenariusz wyszukiwania i lista starego monitora zostały sprawdzone w prawdziwym Telegramie przez Safari. W rozmowie widoczny był również rzeczywisty alert o nowych ofertach i spadku ceny. Wynik testu adaptera: `live-roundtrip-test.json`; ceny są wyłącznie historyczną obserwacją z czasu sprawdzenia.

## Ograniczenia

- Jeden dorosły, economy, bez dodatkowego bagażu. Samodzielne przesiadki wyłączone parametrem strony. Taryfę i ostateczną cenę należy sprawdzić u sprzedawcy.
- Ruchome okno: dziś plus kolejne N−1 dni, maks. 180 dni. Pobyt: 1–90 nocy według filtra Flightlist. Dane strony mogą pochodzić z jej cache; brak gwarancji ceny i dostępności w rezerwacji.
- Maks. 100 ofert w odpowiedzi. Nie wysyłamy alertów „zniknęło”, bo brak w pierwszej setce nie dowodzi wyprzedania. Ponowne wejście do pierwszej setki może być oznaczone jako nowość.
- Domyślny interwał monitorów: godzina; minimalny 15 minut. Cache klienta: 5 minut, do 128 wyszukiwań; odstęp zapytań: min. 2 sekundy. To ustawienia prototypu, nie ustalone limity dostawcy.
- Ceny wybiera niesesyjny cookie preferencji `currency=PLN` lub odpowiednik. Bot nie kopiuje logowania, tokenów sesji ani cookies ochronnych z przeglądarki. Po otwarciu linku wyników w przeglądarce jej własna preferencja waluty może być inna; wybierz właściwą walutę w menu strony.
- Błąd sieci, 403/429, niespójny HTML i inna waluta niż zamówiona nie nadpisują stanu. Nie obchodzimy blokad. Nie ma jeszcze osobnych alertów administracyjnych o awarii źródła.
- Kolejka przetrwa restart. Awaria między wysłaniem a potwierdzeniem w SQLite może spowodować duplikat. Nie ma gwarancji exactly-once.
- Integracja opiera się na HTML strony i zgodzie zadeklarowanej przez użytkownika. Nie jest wersjonowanym oficjalnym API. Alternatywy: `RESEARCH.md`.

Przyciski obserwacji, ręcznego sprawdzania, usuwania i przywracania zostały sprawdzone na żywo w Safari. Dodatkowy monitor testowy wyłączono; pierwotny #1 pozostał aktywny.

Kolejny etap: wybór filtrów krok po kroku oraz przeniesienie usługi na dostępny serwer dla pracy całodobowej.

Naturalne wyszukiwanie zweryfikowano na żywym adapterze dla PL → PH, Warszawa → Manila i Gdańsk → Hamburg; każda trasa zwróciła 100 ofert. Zapis: `live-natural-test.json`.

W Safari/Telegram potwierdzono odpowiedzi na zwykłe zdania Warszawa → Manila (pierwsza oferta 1332 PLN) i Gdańsk → Hamburg (105 PLN), bez kodów i dat. Zrzuty w katalogu nadrzędnym `outputs/`. Ceny z czasu testu, bez gwarancji utrzymania.
