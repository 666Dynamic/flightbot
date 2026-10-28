# Research integracji — 27.10.2026

## Flightlist: rekomendowane źródło prototypu

Inspekcja CDP poprzedniej sesji potwierdziła endpoint podpowiedzi `/api/locations?locale=en&query=Poland` zwracający `{results:[{value:"PL",text:"Poland",type:"country"}]}`. Wyszukiwanie wysyła GET do strony głównej z parametrami `from`, `to`, `trip`, `depart_from`, `depart_to` itd. Nagłówki `X-Fragment: 1`, `Accept: text/html` wybierają fragment HTML. Kod klienta wspomina Tequilę oraz filtrowanie cache po stronie serwera; samego połączenia backend → Kiwi nie widać w przeglądarce.

Dzisiejszy test poza przeglądarką: HTTP 200 bez cookies i kluczy. Adapter odczytał 100 ofert dla PL → TH. Nie oznacza to 100 wszystkich lotów ani gwarancji ceny w rezerwacji. Nie ustalono TTL cache serwera, przyznanych limitów ani gwarancji dostępności. Nie opieramy się na założeniu, że źródło jest bezpłatnym publicznym API.

Użytkownik potwierdził posiadanie zgody na integrację. Nie kontaktowano się z Flightlist. Publiczny [regulamin](https://www.flightlist.io/terms/) zawiera ograniczenie automatycznego zbierania; w tym projekcie podstawą jest deklarowana zgoda użytkownika. [Opis produktu](https://www.flightlist.io/about/) wskazuje historyczne użycie Kiwi API.

## Alternatywy

### Kiwi / Tequila

Najbardziej zbliżony model danych do Flightlist: elastyczne geografie i wyszukiwanie agregowane. [Dokumentacja programu Travelpayouts](https://support.travelpayouts.com/hc/en-us/articles/360019237899-Kiwi-com-affiliate-program-API) opisuje obsługę krajów, miast i regionów. Wymaga minimum 50 tys. MAU dla dostępu przez ten program; nie jest to dowód identycznych wymagań wszystkich umów bezpośrednich z Kiwi. Koszt i dostęp dla prywatnego bota nie zostały potwierdzone. Nie zakładać, że stare poradniki z darmowym kluczem nadal obowiązują.

### Skyscanner

Najciekawsza alternatywa do odkrywania tras. [Indicative Prices](https://developers.skyscanner.net/docs/flights-indicative-prices/overview) korzysta z cache do czterech dni, więc nie jest źródłem gwarantowanych aktualnych cen. [Przykłady agregacji](https://developers.skyscanner.net/docs/flights-indicative-prices/use-cases) obejmują kraje, lecz obsługa kombinacji geografii i czasu ma ograniczenia; nie każde zapytanie oznacza dowolne ruchome 30 dni. [Dostęp](https://developers.skyscanner.net/docs/getting-started/authentication) wymaga akceptacji partnerstwa; nie potwierdzono publicznej ceny dla prywatnego bota. Co szczególnie ważne, [wytyczne](https://developers.skyscanner.net/docs/getting-started/usage-guidelines) wymagają, by wywołania Live Prices wynikały z działania użytkownika oraz miały dokładne daty i trasę. Nie jest to gotowy zamiennik okresowego monitora Live Prices bez osobnych uzgodnień.

### Duffel

Dobre API do wyszukiwania i zakupu konkretnych podróży, słabsze dopasowanie do skanowania kraj → kraj przez cały miesiąc. [Offer Requests](https://duffel.com/docs/api/v2/offer-requests) opisuje lotniska/miasta oraz daty poszczególnych odcinków. Rozszerzanie na wiele lotnisk i dni zwiększa liczbę zapytań. [Cennik](https://duffel.com/pricing): 0,005 USD za nadmiarowe wyszukiwanie przy przekroczeniu stosunku 1500 wyszukiwań do jednej rezerwacji; dodatkowe opłaty dotyczą zamówień. Sam dostawca zaznacza w [pomocy](https://help.duffel.com/hc/en-gb/articles/360019684099-Is-there-any-limit-to-how-many-searches-I-can-make), że kalendarze i metawyszukiwarki mogą nie pasować do jego modelu. Nie wybieram go do prywatnego monitora bez rezerwowania.

### Amadeus

Znalezione oficjalne materiały opisują cache wybranych par tras w Flight Inspiration/Cheapest Date i osobny Flight Offers Search. [FAQ](https://admin.developers.amadeus.com/self-service/apis-docs/guides/developer-guides/faq/) oraz [opis rozliczeń](https://admin.developers.amadeus.com/self-service/apis-docs/guides/developer-guides/pricing/) wskazują darmowe pule i opłaty za dodatkowe wywołania, zależne od API. Jednak bezpośrednie otwarcie FAQ nie powiodło się z powodu przekierowania, a wyniki indeksu mogą być starsze. Bieżącego onboardingu, dostępności i konkretnych cen nie potwierdzono; dlatego nie rekomenduję teraz integracji z Amadeus na tej podstawie.

## Decyzja

Pozostać przy adapterze Flightlist do prototypu: najbliższe wymaganiom i rzeczywiście sprawdzone bez przeglądarki. Nie wdrażano żadnego alternatywnego API, nie zakładano kont ani nie ponoszono kosztów. Gdyby potrzebne było formalne API ze stabilnym kontraktem, najpierw ustalić bezpośredni dostęp Kiwi; Skyscanner rozważyć dla zapytań eksploracyjnych z cenami orientacyjnymi, z uwzględnieniem zasad monitorowania.
