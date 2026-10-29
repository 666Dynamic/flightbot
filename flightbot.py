#!/usr/bin/env python3
"""Flightlist research prototype. Python 3.9+, standard library only."""
import argparse
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import unicodedata
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE = "https://www.flightlist.io/"
TZ = ZoneInfo("Europe/Berlin")

class ProviderError(RuntimeError):
    pass

@dataclass
class Search:
    origin: str
    destination: str
    days: int = 30
    budget: str = ""
    stops: int = 4
    start: str = ""
    end: str = ""
    currency: str = "USD"
    nights_from: int = 0
    nights_to: int = 0
    origin_name: str = ""
    destination_name: str = ""

    def __post_init__(self):
        for name in ("origin","destination"):
            value=getattr(self,name)
            if re.fullmatch(r"[a-z]{2,3}",value,re.I):setattr(self,name,value.upper())
        # Country/airport codes and provider slugs; never arbitrary URLs.
        for value in (self.origin, self.destination):
            if not re.fullmatch(r"[A-Za-z0-9_,.:-]{2,100}", value):
                raise ValueError("Użyj kodu kraju/lotniska lub identyfikatora lokalizacji.")
        if not 1 <= self.days <= 180 or not 0 <= self.stops <= 4:
            raise ValueError("Zakres: 1–180 dni; przesiadki: 0–4.")
        if self.currency not in ("USD", "PLN", "EUR"):
            raise ValueError("Obsługiwane waluty: USD, PLN, EUR.")
        if bool(self.nights_from) != bool(self.nights_to) or not 0 <= self.nights_from <= self.nights_to <= 90:
            raise ValueError("Pobyt: od 1 do 90 nocy, np. 10-16.")
        if self.budget and (not Decimal(self.budget).is_finite() or Decimal(self.budget) <= 0):
            raise ValueError("Budżet musi być dodatni, w wybranej walucie.")
        if bool(self.start) != bool(self.end):
            raise ValueError("Podaj obie daty.")
        if self.start:
            a, b = date.fromisoformat(self.start), date.fromisoformat(self.end)
            if b < a or (b-a).days > 179:
                raise ValueError("Nieprawidłowy przedział dat.")

    def params(self, today=None):
        today = today or datetime.now(TZ).date()
        start = date.fromisoformat(self.start) if self.start else today
        end = date.fromisoformat(self.end) if self.end else start + timedelta(days=self.days-1)
        if start < today:
            raise ValueError("Data wylotu jest w przeszłości; utwórz nowe wyszukiwanie.")
        params = dict(from_=self.origin, to=self.destination, trip="oneway",
                      depart_from=start.isoformat(), depart_to=end.isoformat(), adults=1,
                      children=0, infants=0, hand_bags=0, hold_bags=0, cabin="M",
                      sort="price", limit=100, depart_time="any", arrive_time="any",
                      self_transfer=0, stops=self.stops)
        if self.nights_from:
            params.update(trip="return", return_from=(start+timedelta(days=self.nights_from)).isoformat(),
                          return_to=(end+timedelta(days=self.nights_to)).isoformat(),
                          nights_from=self.nights_from,nights_to=self.nights_to,return_from_diff=0,return_to_diff=0)
        params["from"] = params.pop("from_")
        return params

@dataclass
class Offer:
    key: str
    price: str
    currency: str
    summary: str
    url: str

@dataclass
class Result:
    offers: list
    count: int
    url: str
    observed_at: str
    truncated: bool
    source: str = "Flightlist"

class Node:
    def __init__(self, tag="root", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []
    def text(self):
        if "hidden" in self.attrs or self.attrs.get("aria-hidden") == "true": return ""
        if self.tag == "img": return self.attrs.get("alt", "")
        return " ".join(x.text() if isinstance(x, Node) else x for x in self.children).strip()
    def find(self, tag):
        out = []
        for child in self.children:
            if isinstance(child, Node):
                if child.tag == tag: out.append(child)
                out.extend(child.find(tag))
        return out

class Tree(HTMLParser):
    VOID = set("area base br col embed hr img input link meta param source track wbr".split())
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs)
        self.stack[-1].children.append(n)
        if tag not in self.VOID: self.stack.append(n)
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID: self.handle_endtag(tag)
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break
    def handle_data(self, data):
        if data.strip(): self.stack[-1].children.append(" ".join(data.split()))


def parse_results(html, search_url=BASE, expected_currency=None):
    tree = Tree(); tree.feed(html)
    regions = [n for n in tree.root.find("div") if n.attrs.get("data-search-region") == "results"]
    if len(regions) != 1:
        raise ProviderError("Brak regionu wyników: blokada, błąd serwera lub zmiana HTML.")
    region = regions[0]
    counts = [re.search(r"^(\d+) flights? found$", n.text()) for n in region.find("h2")]
    counts = [m for m in counts if m]
    if not counts and not region.find("li") and any(n.text() == "No flights found" for n in region.find("strong")):
        # Explicit empty state from the provider, not an error page.
        return Result([], 0, search_url, datetime.now(timezone.utc).isoformat(), False)
    if not counts:
        # Fail closed: an empty/error page is not an empty flight search.
        raise ProviderError("Nie rozpoznano liczby wyników; stan monitorów zachowany.")
    if len(counts) != 1:raise ProviderError("Niejednoznaczna liczba wyników Flightlist.")
    count = int(counts[0][1])
    offers = []
    for item in region.find("li"):
        summaries = item.find("summary")
        if not summaries: continue
        summary = " ".join(summaries[0].text().split())
        price = re.match(r"^(US\$|PLN|EUR|€)\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)\s+", summary)
        if not price:
            raise ProviderError("Nieznany format ceny/waluty; stan monitorów zachowany.")
        if Decimal(price[2].replace(",", ""))<=0:raise ProviderError("Nieprawidłowa cena oferty.")
        currency={"US$":"USD","€":"EUR"}.get(price[1],price[1])
        if expected_currency and currency != expected_currency:
            raise ProviderError("Flightlist zwrócił inną walutę niż zamówiona; nie porównuję tych cen.")
        links = [n.attrs.get("href", "") for n in item.find("a")]
        links = [u for u in links if urlparse(u).scheme == "https" and urlparse(u).hostname in ("www.kiwi.com", "kiwi.com")]
        if not links: raise ProviderError("Oferta bez rozpoznanego linku rezerwacji.")
        url = links[0]
        # Exclude price and volatile booking_token from identity. Include summary
        # to distinguish same itinerary ID with different departure time/date.
        rest = summary[price.end():]
        flight_id = parse_qs(urlparse(url).query).get("flightsId", [""])[0]
        key = hashlib.sha256((flight_id + "|" + rest).encode()).hexdigest()[:24]
        offers.append(Offer(key, str(Decimal(price[2].replace(",", ""))), currency, rest, url))
    if len(offers) != count:
        raise ProviderError(f"HTML niespójny: deklarowano {count}, odczytano {len(offers)}.")
    unique = {o.key:o for o in offers}
    return Result(sorted(unique.values(), key=lambda o:Decimal(o.price)), count,
                  search_url, datetime.now(timezone.utc).isoformat(), count >= 100)

class Flightlist:
    def __init__(self, cache_seconds=300):
        self.cache_seconds, self.cache, self.last_request = cache_seconds, {}, 0.0
        self.place_labels={}
    def get(self, url, accept="text/html", currency="USD"):
        # No cookies, credential reuse, proxies or automatic retries on blocks.
        delay = 2 - (time.monotonic()-self.last_request)
        if delay > 0: time.sleep(delay)
        self.last_request = time.monotonic()
        req = Request(url, headers={"Accept":accept, "X-Fragment":"1", "User-Agent":"FlightMonitor-Research/0.2", "Cookie":"currency="+currency})
        try:
            with urlopen(req, timeout=30) as response:
                if urlparse(response.url).hostname != "www.flightlist.io":
                    raise ProviderError("Nieoczekiwane przekierowanie.")
                body = response.read(5_000_001)
                if len(body) > 5_000_000: raise ProviderError("Odpowiedź zbyt duża.")
                return body.decode("utf-8")
        except HTTPError as e:
            raise ProviderError(f"Flightlist HTTP {e.code}; bez ponawiania/blokady omijania.") from None
        except (OSError, UnicodeError) as e:
            raise ProviderError(f"Błąd połączenia Flightlist ({type(e).__name__}).") from None
    def locations(self, term):
        if not 2 <= len(term) <= 80: raise ValueError("Wpisz 2–80 znaków.")
        cache_key=("location",term.casefold())
        cached=self.cache.get(cache_key)
        if cached and time.monotonic()-cached[0]<3600:return cached[1]
        try:
            data=json.loads(self.get(BASE+"api/locations?"+urlencode({"locale":"en","query":term}),"application/json"))
        except (ValueError, TypeError):
            raise ProviderError("Nieprawidłowa odpowiedź lokalizacji Flightlist. Spróbuj ponownie później.") from None
        if not isinstance(data,dict) or not isinstance(data.get("results"),list):
            raise ProviderError("Nieprawidłowa lista lokalizacji Flightlist. Spróbuj ponownie później.")
        values=[v for v in data.get("results",[]) if isinstance(v,dict) and isinstance(v.get("value"),str) and isinstance(v.get("text"),str)]
        if len(self.cache)>=128:self.cache.pop(next(iter(self.cache)))
        self.place_labels.update({v["value"]:v["text"][:120] for v in values})
        self.cache[cache_key]=(time.monotonic(),values)
        return values
    def search(self, spec, refresh=False):
        url = BASE+"?"+urlencode(spec.params())
        cache_key=(url,spec.currency)
        cached = self.cache.get(cache_key)
        if not refresh and cached and time.monotonic()-cached[0] < self.cache_seconds: return cached[1]
        result = parse_results(self.get(url,currency=spec.currency), url, spec.currency)
        if len(self.cache) >= 128: self.cache.pop(next(iter(self.cache)))
        self.cache[cache_key] = (time.monotonic(),result)
        return result

COUNTRIES = {"polski":"PL","polska":"PL","poland":"PL", "niemiec":"DE","niemcy":"DE", "germany":"DE",
             "tajlandii":"TH","tajlandia":"TH","thailand":"TH", "hiszpanii":"ES","hiszpania":"ES",
             "włoch":"IT","wlochy":"IT","włochy":"IT","portugalii":"PT","portugalia":"PT"}
COUNTRIES.update({"filipin":"PH","filipiny":"PH","philippines":"PH","manili":"MNL","manila":"MNL","japonii":"JP","japonia":"JP","tokio":"TYO","wietnamu":"VN","wietnam":"VN","malezji":"MY","malezja":"MY","indonezji":"ID","indonezja":"ID","bali":"DPS"})
HELP = """Oferty: Kiwi.com. Wybierz filtry przyciskami.

✈️ /start — zacznij wyszukiwanie przyciskami.
🧭 /kreator — nowe wyszukiwanie krok po kroku.

✈️ Albo napisz po prostu:
„Najtańsze loty z Polski do Filipin”
„Wyszukaj mi loty z Warszawy do Manili”
„Z Gdańska do Hamburga”
Bez dat: następne 30 dni, jedna strona, PLN. Niejednoznaczne miejsca pokażę do wyboru.

Polecenia skrótowe:
/szukaj PL TH — najtańsze w następnych 30 dniach (USD)
/szukaj PL TH 30 500 1 USD
/szukaj PL TH 30 4000 1 PLN 10-16
/obserwuj PL TH 30 4000 1 PLN 10-16
Kolejno: skąd, dokąd, dni do wylotu, budżet, maks. przesiadki, waluta, opcjonalnie pobyt w nocach.
Bez pobytu: jedna strona. Z pobytem: tam i z powrotem; budżet za całą podróż.
Stare polecenia bez waluty nadal oznaczają USD.
/lokalizacje nazwa — znajdź kod kraju, regionu lub miasta
/lista — Twoje monitory • /usun ID — usuń monitor
Przykład: pokaż loty z Polski do Tajlandii w następnych 30 dniach do 4000 PLN, pobyt 10-16 nocy, maksymalnie 1 przesiadka
Następne 30 dni obejmuje dziś i kolejne 29 dni. 1 dorosły, economy, bez dodatkowego bagażu.
Nie musisz znać kodów lotnisk. Przy nieznanych filtrach poproszę o doprecyzowanie."""

# Natural requests keep unresolved names outside the validated provider Search.
@dataclass
class NaturalSearch(Search):
    def __post_init__(self):
        fields=asdict(self);fields.update(origin="PL",destination="TH")
        Search(**fields)
        for value in (self.origin,self.destination):
            if not 2<=len(value)<=80 or re.search(r"[<>/\\]",value):raise ValueError("Podaj nazwę miasta, lotniska, kraju lub regionu.")
    def params(self,today=None):raise ValueError("Najpierw rozpoznaj lokalizacje.")

def norm_place(value):
    value=value.casefold().replace("ł","l")
    return "".join(c for c in unicodedata.normalize("NFKD",value) if not unicodedata.combining(c)).strip()

# Minimal city aliases - only for cities where Flightlist API expects English names
CITY_ALIASES={"warszawa":"Warsaw","warszawy":"Warsaw","warszawie":"Warsaw","rzym":"Rome","rzymu":"Rome",
              "wieden":"Vienna","wiednia":"Vienna","nowego jorku":"New York","nowy jork":"New York",
              "pragi":"Prague","praga":"Prague","mediolanu":"Milan","mediolan":"Milan"}
CITY_ALIASES.update({
    "wroclaw":"Wrocław", "wroclawia":"Wrocław", "krakowa":"Kraków", "krakow":"Kraków",
    "poznania":"Poznań", "poznan":"Poznań", "lodzi":"Łódź", "lodz":"Łódź",
    "gdanska":"Gdańsk", "gdansk":"Gdańsk", "katowic":"Katowice", "katowice":"Katowice",
    "szczecina":"Szczecin", "szczecin":"Szczecin", "berlina":"Berlin",
    "monachium":"Munich", "paryza":"Paris", "paryz":"Paris", "londynu":"London", "londyn":"London",
})
COUNTRY_ONLY={k:v for k,v in COUNTRIES.items() if len(v)==2}

def resolve_place(value):
    value=value.strip()
    return {norm_place(k):v for k,v in COUNTRY_ONLY.items()}.get(norm_place(value),value.upper() if re.fullmatch(r"[a-z]{2,3}",value,re.I) else value)

def location_choices(provider, value):
    """Resolve exact places; never choose a fuzzy hit without confirmation."""
    value = value.strip()
    known = WIZARD_KNOWN.get(norm_place(value))
    if known:
        return known, []
    if re.fullmatch(r"[A-Z]{2,3}|city:[A-Z]{3}", value):
        return value, []
    query = CITY_ALIASES.get(norm_place(value), value)
    options = provider.locations(query)
    options = list({o["value"]: o for o in options if isinstance(o, dict)
                    and isinstance(o.get("text"), str) and o["text"].strip()
                    and isinstance(o.get("value"), str)
                    and re.fullmatch(r"[A-Za-z0-9_,.:-]{2,100}", o["value"])}.values())
    if not options:
        raise ValueError(f"Nie znalazłem miejsca „{value}”. Wpisz nazwę bez odmiany, np. Wrocław, lub kod lotniska.")
    def matches(option):
        name = norm_place(re.split(r"[,(]", option["text"])[0])
        forms = {name, *(name + suffix for suffix in ("a", "u", "y", "i", "e"))}
        if name.endswith("a"):
            forms.update(name[:-1] + suffix for suffix in ("y", "i", "ii", "e"))
        return norm_place(query) in forms
    exact = [o for o in options if matches(o)]
    cities = [o for o in exact if o.get("type") == "city"]
    if len(cities) == 1:
        return cities[0]["value"], []
    if len(cities) > 1:
        return None, cities[:6]
    if len(exact) == 1:
        return exact[0]["value"], []
    return None, (exact or options)[:6]


def parse_command(text, provider=None):
    """
    Parse natural language flight search command.
    Returns (action, arg) where action is one of: search, watch, locations, list, delete, help
    """
    text = text.strip()
    if not text:
        return "help", None

    # Handle slash commands first
    parts = text.split()
    command = parts[0].split("@")[0].lower()

    if command in ("/szukaj", "/obserwuj"):
        if not 3 <= len(parts) <= 8:
            raise ValueError(HELP)
        # /szukaj ORIGIN DEST [days] [budget] [stops] [currency] [nights_min-max]
        origin = parts[1]
        dest = parts[2]
        days = int(parts[3]) if len(parts) > 3 else 30
        budget = parts[4] if len(parts) > 4 else ""
        stops = int(parts[5]) if len(parts) > 5 else 4
        currency = parts[6].upper() if len(parts) > 6 else "USD"
        nights_from = nights_to = 0
        if len(parts) > 7:
            m = re.fullmatch(r"(\d+)[-–](\d+)", parts[7])
            if not m:
                raise ValueError("Pobyt zapisz jako min-max nocy, np. 10-16.")
            nights_from, nights_to = map(int, m.groups())
            if min(nights_from, nights_to) < 1:
                raise ValueError("Pobyt musi wynosić przynajmniej 1 noc.")
        action = "watch" if command == "/obserwuj" else "search"
        return action, Search(
            origin, dest, days, budget, stops,
            currency=currency, nights_from=nights_from, nights_to=nights_to
        )

    if command == "/lokalizacje":
        return "locations", " ".join(parts[1:]) if len(parts) > 1 else ""

    if command == "/lista":
        return "list", None

    if command == "/start":return "wizard", None

    if command in ("/kreator", "/nowe") or text.lower() in ("kreator", "nowe wyszukiwanie"):
        return "wizard", None

    if command == "/usun" and len(parts) == 2:
        try:
            return "delete", int(parts[1])
        except ValueError:
            raise ValueError("Nieprawidłowy numer monitora. Użyj: /usun 123")

    if command in ("/help", "pomoc") or text.lower().strip() in ("co dalej", "ok co dalej", "hej", "cześć", "czesc"):
        return "help", None

    # Extract supported filters before the route: date/budget "do" is not a destination.
    low = " ".join(text.casefold().strip().rstrip(".!?").split())
    is_watch = bool(re.match(r"^(?:proszę |prosze )?obserwuj(?:ę|e)?\b", low))
    low = re.sub(r"^(?:proszę |prosze )?obserwuj(?:ę|e)?\s+", "", low)
    values = dict(days=30, budget="", currency="PLN", stops=4, start="", end="",
                  nights_from=0, nights_to=0)
    def extract(pattern):
        nonlocal low
        found = list(re.finditer(pattern, low))
        if len(found) > 1:
            raise ValueError("Podaj jeden zakres lub jedną wartość każdego filtra.")
        if not found:
            return None
        match = found[0]
        low = low[:match.start()] + " " + low[match.end():]
        return match
    day = r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)"
    dates = extract(r"\bod\s+(" + day + r")\s+(?:do|[-–])\s+(" + day + r")(?![\d./-])")
    period = extract(r"\b(?:w\s+)?(?:następnych|nastepnych|najbliższych|najblizszych)\s+(\d+)\s+(?:dniach|dni)\b")
    month = extract(r"\bw\s+(?:następnym|nastepnym|przyszłym|przyszlym)\s+miesi[ąa]cu\b")
    month_names = ["styczniu", "lutym", "marcu", "kwietniu", "maju", "czerwcu", "lipcu", "sierpniu", "wrześniu", "październiku", "listopadzie", "grudniu"]
    named = extract(r"\bw\s+(" + "|".join(month_names) + r")(?:\s+(20\d{2}))?\b")
    if sum(bool(x) for x in (dates, period, month, named)) > 1:
        raise ValueError("Podaj jeden okres wylotu: daty, miesiąc albo następne N dni.")
    today = datetime.now(TZ).date()
    if dates:
        values["start"], values["end"] = parse_date_range(dates[1] + " – " + dates[2], today)
    elif period:
        values["days"] = int(period[1])
    elif month:
        values["start"], values["end"] = next_month(today)
    elif named:
        number = month_names.index(named[1]) + 1
        year = int(named[2]) if named[2] else today.year + (number < today.month)
        first = date(year, number, 1)
        last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        if last < today:
            raise ValueError("Wybrany miesiąc jest w przeszłości.")
        values["start"], values["end"] = max(first, today).isoformat(), last.isoformat()
    stay = extract(r"\b(?:pobyt|na)\s+(\d+)(?:\s*[-–]\s*(\d+))?\s+(nocy|noce|noc|dni)\b")
    if stay:
        if stay[3] == "dni":
            raise ValueError("„Na N dni” oznacza długość pobytu czy termin wylotu? Napisz „pobyt N nocy” albo „w następnych N dniach”.")
        values["nights_from"] = int(stay[1])
        values["nights_to"] = int(stay[2] or stay[1])
        if values["nights_from"] < 1:
            raise ValueError("Pobyt musi wynosić przynajmniej 1 noc.")
    budget = extract(r"\b(?:do|budżet|budzet)\s+(\d+(?:[ \u00a0]\d{3})*(?:[.,]\d{1,2})?)\s*(pln|usd|eur|zł|zl|€|\$)(?!\w)")
    currency = extract(r"\bw\s+(pln|usd|eur)\b")
    if budget:
        values["budget"] = budget[1].replace(" ", "").replace("\u00a0", "").replace(",", ".")
        values["currency"] = {"zł":"PLN", "zl":"PLN", "€":"EUR", "$":"USD"}.get(budget[2], budget[2].upper())
    if currency:
        if budget and currency[1].upper() != values["currency"]:
            raise ValueError("Waluta budżetu różni się od waluty wyników.")
        values["currency"] = currency[1].upper()
    direct = extract(r"\b(?:bez\s+przesiad(?:ek|ki)|bezpośredni(?:o|e)?|bezposredni(?:o|e)?|direct)\b")
    stops = extract(r"\b(?:maksymalnie|maks\.?|max\.?)\s+(\d+)\s+przesiad(?:ka|ki|ek|kę)\b")
    if direct and stops and int(stops[1]) != 0:
        raise ValueError("Wybierz lot bezpośredni albo maksymalną liczbę przesiadek.")
    values["stops"] = 0 if direct else int(stops[1]) if stops else 4
    returning = extract(r"\b(?:tam\s+i\s+z\s+powrotem|w\s+obie\s+strony|return|round\s*trip)\b")
    oneway = extract(r"\b(?:w\s+jedn[ąa]\s+stron[ęe]|one\s*way|tylko\s+tam)\b")
    if oneway and (returning or stay):
        raise ValueError("Wybierz: jedną stronę albo pobyt z powrotem.")
    if returning and not stay:
        raise ValueError("Na ile nocy chcesz polecieć? Dopisz np. „pobyt 10-16 nocy”.")
    # Remove only known conversational framing, never unknown requirements.
    noise = r"\b(?:najtańsze|najtansze|najtańszy|najtanszy|tanie|tani|loty|lotów|lotow|bilety|bilet|szukaj|wyszukaj|pokaż|pokaz|znajdź|znajdz|proszę|prosze|chcę|chce|chciałbym|chcialbym|czy|możesz|mozesz|mi)\b"
    low = " ".join(re.sub(noise, " ", low).split()).strip(" ,;")
    route = re.fullmatch(r"(?:(?:z|ze)\s+)?(.+?)\s*(?:\sdo\s|->|→|=>|\sto\s)\s*(.+)", low)
    if not route:
        raise ValueError("Nie rozumiem, skąd i dokąd chcesz polecieć. Przykłady: „z Warszawy do Manili”, „Polska → Filipiny”. Użyj /kreator, aby wybrać filtry.")
    origin, dest = (" ".join(x.split()).strip(" ,;") for x in route.groups())
    for value in (origin, dest):
        if re.search(r"\d|[,;]|\b(?:z|ze|do|od|w|na|bez|maks|max|pobyt|bagażem|bagazem)\b", value):
            raise ValueError("Nie rozpoznaję wszystkich filtrów. Nie pomijam ich: użyj /kreator albo podaj np. „z Polski do Filipin w następnych 30 dniach do 3000 PLN”. Dodatkowy bagaż nie jest obsługiwany.")
    return ("watch" if is_watch else "search"), NaturalSearch(
        origin=resolve_place(origin), destination=resolve_place(dest), **values)


def describe_search(spec):
    trip=f"tam i z powrotem · {spec.nights_from}–{spec.nights_to} nocy" if spec.nights_from else "jedna strona"
    budget=f"do {spec.budget} {spec.currency}" if spec.budget else f"bez limitu ceny ({spec.currency})"
    stops="bez przesiadek" if spec.stops==0 else ("maks. 1 przesiadka" if spec.stops==1 else f"maks. {spec.stops} przesiadki")
    pl=lambda iso:date.fromisoformat(iso).strftime("%d.%m.%Y")
    period=f"{pl(spec.start)} – {pl(spec.end)}" if spec.start else f"następne {spec.days} dni"
    def label(code,name):
        known={"PL":"Polska","PH":"Filipiny","TH":"Tajlandia","DE":"Niemcy","ES":"Hiszpania","IT":"Włochy","JP":"Japonia","VN":"Wietnam","MY":"Malezja","ID":"Indonezja"}
        return name or known.get(code,code.removeprefix("city:"))
    return f"{label(spec.origin,spec.origin_name)} → {label(spec.destination,spec.destination_name)} · {period}\n{trip} · {budget} · {stops}"

def search_identity(spec):
    return {k:v for k,v in asdict(spec).items() if k not in ("origin_name","destination_name")}

class Reply(str):
    def __new__(cls,text,markup=None):
        value=super().__new__(cls,text);value.markup=markup;value.edit=False;value.edit_id=None;return value

def keyboard(*rows):return {"inline_keyboard":list(rows)}
def button(text,data):return {"text":text,"callback_data":data}

class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS watches(id INTEGER PRIMARY KEY, owner TEXT NOT NULL, spec TEXT NOT NULL, UNIQUE(owner,spec));
        CREATE TABLE IF NOT EXISTS snapshots(watch INTEGER PRIMARY KEY, data TEXT NOT NULL, observed TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY, owner TEXT NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS actions(token TEXT PRIMARY KEY, owner TEXT NOT NULL, spec TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS place_requests(token TEXT PRIMARY KEY,owner TEXT NOT NULL,data TEXT NOT NULL,expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS wizards(owner TEXT PRIMARY KEY,data TEXT NOT NULL,expires INTEGER NOT NULL);

        """)
        if "source" not in [r[1] for r in self.db.execute("PRAGMA table_info(snapshots)")]:
            self.db.execute("ALTER TABLE snapshots ADD COLUMN source TEXT NOT NULL DEFAULT 'Flightlist'")
        if "active" not in [r[1] for r in self.db.execute("PRAGMA table_info(watches)")]:
            self.db.execute("ALTER TABLE watches ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        if "markup" not in [r[1] for r in self.db.execute("PRAGMA table_info(outbox)")]:
            self.db.execute("ALTER TABLE outbox ADD COLUMN markup TEXT")
        if "edit_id" not in [r[1] for r in self.db.execute("PRAGMA table_info(outbox)")]:
            self.db.execute("ALTER TABLE outbox ADD COLUMN edit_id INTEGER")
        self.db.commit()
    def wizard_get(self,owner):
        row=self.db.execute("SELECT data FROM wizards WHERE owner=? AND expires>=?",(owner,int(time.time()))).fetchone()
        return json.loads(row[0]) if row else None
    def wizard_set(self,owner,data):
        data["token"]=secrets.token_hex(6)
        with self.db:self.db.execute("INSERT OR REPLACE INTO wizards VALUES(?,?,?)",(owner,json.dumps(data),int(time.time())+86400))
    def wizard_clear(self,owner):
        with self.db:self.db.execute("DELETE FROM wizards WHERE owner=?",(owner,))
    def place_request(self,owner,spec,intent,field,choices):
        token=secrets.token_hex(10)
        data={"spec":asdict(spec),"intent":intent,"field":field,"choices":choices}
        with self.db:
            self.db.execute("DELETE FROM place_requests WHERE expires < ?",(int(time.time()),))
            self.db.execute("INSERT INTO place_requests VALUES(?,?,?,?)",(token,owner,json.dumps(data),int(time.time())+3600))
        return token
    def choose_place(self,owner,token,index):
        row=self.db.execute("SELECT data FROM place_requests WHERE owner=? AND token=? AND expires>=?",(owner,token,int(time.time()))).fetchone()
        if not row:raise ValueError("Wybór miejsca wygasł. Wpisz wyszukiwanie ponownie.")
        data=json.loads(row[0])
        if not 0<=index<len(data["choices"]):raise ValueError("Nieprawidłowy wybór miejsca.")
        data["spec"][data["field"]]=data["choices"][index]["value"]
        data["spec"][data["field"]+"_name"]=data["choices"][index]["text"][:120]
        return NaturalSearch(**data["spec"]),data["intent"]
    def action(self,owner,spec):
        token=secrets.token_hex(12)
        with self.db:
            self.db.execute("DELETE FROM actions WHERE expires < ?",(int(time.time()),))
            self.db.execute("INSERT INTO actions VALUES(?,?,?,?)",(token,owner,json.dumps(asdict(spec)),int(time.time())+604800))
        return token
    def action_spec(self,owner,token):
        row=self.db.execute("SELECT spec FROM actions WHERE owner=? AND token=? AND expires>=?",(owner,token,int(time.time()))).fetchone()
        if not row:raise ValueError("Ten przycisk wygasł lub należy do innego czatu. Wyszukaj loty ponownie.")
        return Search(**json.loads(row[0]))
    def watch(self,owner,wid,include_inactive=False):
        row=self.db.execute("SELECT * FROM watches WHERE owner=? AND id=?",(owner,wid)).fetchone()
        if not row or (not row["active"] and not include_inactive):raise ValueError("Ten monitor nie jest aktywny lub należy do innego czatu.")
        return row
    def restore(self,owner,wid):
        row=self.watch(owner,wid,True)
        if not row["active"] and len(self.watches(owner))>=20:raise ValueError("Limit 20 aktywnych monitorów.")
        with self.db:self.db.execute("UPDATE watches SET active=1 WHERE owner=? AND id=?",(owner,wid))
        return wid
    def add(self, owner, spec, result):
        encoded=json.dumps(asdict(spec),sort_keys=True)
        # Existing v1 specs omit currency/stay fields; compare normalized objects.
        for existing in self.db.execute("SELECT * FROM watches WHERE owner=?",(owner,)).fetchall():
            if search_identity(Search(**json.loads(existing["spec"]))) == search_identity(spec):
                if not existing["active"]:
                    self.restore(owner,existing["id"])
                    with self.db:self.db.execute("INSERT OR REPLACE INTO snapshots(watch,data,observed,source) VALUES(?,?,?,?)",(existing["id"],json.dumps({o.key:asdict(o) for o in result.offers}),result.observed_at,result.source))
                return existing["id"]
        if len(self.watches(owner)) >= 20 and not self.db.execute("SELECT 1 FROM watches WHERE owner=? AND spec=?",(owner,encoded)).fetchone():
            raise ValueError("Limit 20 monitorów na użytkownika.")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO watches(owner,spec) VALUES(?,?)",(owner,encoded))
            wid=self.db.execute("SELECT id FROM watches WHERE owner=? AND spec=?",(owner,encoded)).fetchone()[0]
            self.db.execute("INSERT OR IGNORE INTO snapshots(watch,data,observed,source) VALUES(?,?,?,?)",(wid,json.dumps({o.key:asdict(o) for o in result.offers}),result.observed_at,result.source))
        return wid
    def watches(self, owner=None):
        if owner is None: return self.db.execute("SELECT * FROM watches WHERE active=1").fetchall()
        return self.db.execute("SELECT * FROM watches WHERE owner=? AND active=1",(owner,)).fetchall()
    def delete(self, owner, wid):
        with self.db:
            found=self.db.execute("UPDATE watches SET active=0 WHERE owner=? AND id=? AND active=1",(owner,wid)).rowcount
        return found
    def enqueue(self,owner,body):
        self.db.execute("INSERT INTO outbox(owner,body,markup,edit_id) VALUES(?,?,?,?)",(owner,str(body),json.dumps(body.markup) if getattr(body,"markup",None) else None,getattr(body,"edit_id",None)))
    def scan(self, provider):
        errors=[]
        for row in self.watches():
            try: result=provider.search(Search(**json.loads(row["spec"])))
            except (ProviderError,ValueError) as e:
                errors.append({"watch":row["id"],"error":str(e)}); continue
            spec=Search(**json.loads(row["spec"]))
            oldrow=self.db.execute("SELECT data,source FROM snapshots WHERE watch=?",(row["id"],)).fetchone()
            old=json.loads(oldrow[0]) if oldrow else {}
            current={o.key:asdict(o) for o in result.offers}
            alerts=[]
            for o in result.offers:
                if spec.budget and Decimal(o.price)>Decimal(spec.budget): continue
                prev=old.get(o.key)
                if not prev: alerts.append("Nowa oferta: "+offer_line(o))
                elif prev["currency"]==o.currency and Decimal(o.price)<Decimal(prev["price"]):
                    alerts.append(f"Spadek {prev['price']} → {o.price} {o.currency}: "+offer_line(o))
            with self.db:
                if oldrow and oldrow["source"]==result.source and alerts:
                    self.enqueue(row["owner"],f"Monitor #{row['id']}\n"+"\n".join(alerts[:5])+f"\nZmian: {len(alerts)}. Wyniki dostawcy mogą obejmować tylko część ofert.\n{result.url}")
                self.db.execute("INSERT OR REPLACE INTO snapshots(watch,data,observed,source) VALUES(?,?,?,?)",(row["id"],json.dumps(current),result.observed_at,result.source))
        return errors

def offer_line(o):
    # Link to provider search included separately; avoid huge, expiring booking tokens in messages.
    summary=o.summary.replace("Outbound:","Wylot:").replace("Return:","Powrót:").replace("Flight details","")
    summary=re.sub(r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d+)(?:st|nd|rd|th) (\d{4})",
        lambda m:f"{int(m[2]):02d}.{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'].index(m[1])+1:02d}.{m[3]}",summary)
    summary=re.sub(r"(\d{1,2}):(\d{2})(am|pm)",lambda m:f"{int(m[1])%12+(12 if m[3]=='pm' else 0):02d}:{m[2]}",summary)
    summary=summary.replace("Direct","bez przesiadek").replace("1 Stop","1 przesiadka")
    summary=re.sub(r"(\d+) Stops",r"\1 przesiadki",summary)
    summary=summary.replace("Powrót:","\n    Powrót:")
    return f"{o.price} {o.currency} — {summary.strip()}"

def render(result,spec):
    offers=[o for o in result.offers if not spec.budget or Decimal(o.price)<=Decimal(spec.budget)]
    lines=["✈️ "+describe_search(spec), "1 dorosły · economy · bez dodatkowego bagażu", ""]
    lines.extend(f"{i}. {offer_line(o)}\n" for i,o in enumerate(offers[:5],1))
    if not result.count: lines.append("Brak lotów dla tych filtrów. Spróbuj szerszych dat, większej liczby przesiadek albo całego kraju/regionu.\n")
    elif not offers: lines.append("Brak ofert w budżecie w otrzymanym zestawie wyników.")
    if result.truncated: lines.append("Wybrane wyniki Kiwi.com — lista może być niepełna." if result.source=="Kiwi.com" else "Limit 100 wyników — to nie jest pełna lista wszystkich lotów.")
    lines.append("Źródło: "+result.source)
    lines.extend(["Cena może się zmienić przed rezerwacją. Samodzielne przesiadki wyłączone.", result.url])
    return "\n".join(lines)

# Step-by-step search form. Buttons carry the field they set ("wz:<field>:<value>"),
# so pressing an older button in the same message cannot corrupt another step.
WIZARD_ORIGINS=[("Polska","PL"),("Warszawa","city:WAW"),("Kraków","city:KRK"),("Gdańsk","city:GDN"),
                ("Katowice","city:KTW"),("Wrocław","city:WRO"),("Poznań","city:POZ"),("Berlin","city:BER")]
WIZARD_DESTINATIONS=[("🌍 Europa","europe"),("🌏 Azja","asia"),("Hiszpania","ES"),("Włochy","IT"),
                     ("Grecja","GR"),("Portugalia","PT"),("Tajlandia","TH"),("Filipiny","PH")]
WIZARD_NIGHTS=["2-4","3-5","5-8","7-10","10-14","14-21"]
WIZARD_BUDGETS=["300","500","1000","2000","3000","5000"]
WIZARD_ORIGINS += [("Łódź","city:LCJ"),("Szczecin","city:SZZ"),("Rzeszów","city:RZE"),("Lublin","city:LUZ"),
                   ("Niemcy — cały kraj","DE"),("Czechy — cały kraj","CZ"),("Praga","city:PRG"),("Wiedeń","city:VIE")]
WIZARD_DESTINATIONS += [("Manila","city:MNL"),("Bangkok","city:BKK"),("Bali (Denpasar)","DPS"),("Hamburg","city:HAM"),
                       ("Londyn — Wielka Brytania","city:LON"),("Paryż","city:PAR"),("Rzym","city:ROM"),("Nowy Jork","city:NYC"),
                       ("Japonia","JP"),("Wietnam","VN"),("Indonezja","ID"),("Malezja","MY"),("Turcja","TR"),("Egipt","EG"),
                       ("Niemcy","DE"),("Polska","PL"),("USA","US"),("Wielka Brytania","GB"),("Norwegia","NO"),("Szwecja","SE"),
                       ("Francja","FR"),("Chorwacja","HR"),("Cypr","CY"),("Malta","MT")]
WIZARD_STEPS=["trip","origin","destination","when","nights","stops","budget","summary"]
WIZARD_TEXT_STEPS={"origin","destination","when","nights","budget"}
# Typed Polish country names (and Bali, which the provider matches to Balikpapan).
WIZARD_KNOWN={norm_place(k):v for k,v in COUNTRIES.items() if len(v)==2 or k=="bali"}
WIZARD_FIELDS={"o":"origin","d":"destination","w":"when","t":"trip","n":"nights","s":"stops","b":"budget"}

def wizard_new():
    return {"version":2,"page":0,"input":False,"step":"trip","edit":False,"origin":"","origin_name":"","destination":"","destination_name":"",
            "days":30,"start":"","end":"","trip":"","nights_from":0,"nights_to":0,"stops":4,"budget":"","choices":[]}

def wizard_next(data,field):
    if data["edit"] and field=="trip" and data["nights_from"]==0 and data.get("trip")=="return":return "nights"
    if data["edit"]:return "summary"
    i=WIZARD_STEPS.index(field)+1
    if WIZARD_STEPS[i]=="nights" and data.get("trip")!="return":i+=1
    return WIZARD_STEPS[i]

def wizard_prev(data):
    i=WIZARD_STEPS.index(data["step"])-1
    if i>=0 and WIZARD_STEPS[i]=="nights" and data.get("trip")!="return":i-=1
    return WIZARD_STEPS[max(i,0)]

def wizard_spec(data):
    if data.get("trip") not in ("oneway","return") or not data["origin"] or not data["destination"]:
        raise ValueError("Dokończ wybór typu podróży i trasy.")
    if data["origin"] == data["destination"]:raise ValueError("Wybierz różne miejsca wylotu i przylotu.")
    if data["trip"] == "return" and not data["nights_from"]:raise ValueError("Wybierz długość pobytu.")
    return Search(data["origin"],data["destination"],days=data["days"],budget=data["budget"],stops=data["stops"],
                  start=data["start"],end=data["end"],currency="PLN",nights_from=data["nights_from"],
                  nights_to=data["nights_to"],origin_name=data["origin_name"],destination_name=data["destination_name"])

def next_month(today=None):
    today=today or datetime.now(TZ).date()
    first=(today.replace(day=1)+timedelta(days=32)).replace(day=1)
    last=(first+timedelta(days=32)).replace(day=1)-timedelta(days=1)
    return first.isoformat(),last.isoformat()

def parse_date_range(text, today=None):
    today = today or datetime.now(TZ).date()
    token = r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)"
    match = re.fullmatch(r"\s*(" + token + r")\s*[-–]\s*(" + token + r")\s*", text)
    if not match:
        raise ValueError("Wpisz daty jako od-do, np. 10.12-20.12 albo 10.12.2026-05.01.2027.")
    def parse(value, year):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value), True
        parts = value.replace("/", ".").split(".")
        explicit = len(parts) == 3
        y = int(parts[2]) if explicit else year
        if explicit and y < 100:
            y += 2000
        return date(y, int(parts[1]), int(parts[0])), explicit
    try:
        start, explicit = parse(match[1], today.year)
        if not explicit and start < today:
            start, _ = parse(match[1], today.year + 1)
        end, explicit_end = parse(match[2], start.year)
        if not explicit_end and end < start:
            end, _ = parse(match[2], start.year + 1)
    except ValueError:
        raise ValueError("Nieprawidłowa data. Przykład: 10.12-20.12.") from None
    if start < today:
        raise ValueError("Data wylotu jest w przeszłości.")
    if end < start or (end-start).days > 179:
        raise ValueError("Zakres dat: koniec po początku, maks. 180 dni.")
    return start.isoformat(), end.isoformat()


def looks_like_sentence(text):
    low=text.casefold()
    return bool(re.match(r"(loty|lot|wyszukaj|szukaj|pokaż|pokaz|znajdź|znajdz|najtańsze|najtansze|obserwuj)\b",low)
                or re.search(r"(?:\sdo\s|->|→|=>|\sto\s)",low))

class Bot:
    def __init__(self,provider,store): self.provider,self.store=provider,store
    def wizard_prompt(self,data,note=""):
        step=data["step"];nav=[button("⬅️ Wstecz","wz:back"),button("✖️ Anuluj","wz:x")]
        def grid(items,field,cols=2):
            buttons=[button(label,f"wz:{field}:{value}") for label,value in items]
            return [buttons[i:i+cols] for i in range(0,len(buttons),cols)]
        if data.get("input"):
            labels={"origin":"miasto, lotnisko lub kraj wylotu", "destination":"miasto, kraj lub region docelowy",
                    "when":"daty wylotu, np. 10.12-20.12", "nights":"zakres nocy, np. 6-9", "budget":"kwotę w PLN, np. 1500"}
            text="✍️ Wpisz tylko "+labels[step]+".\nMożesz też wrócić do przycisków."
            rows=[]
        elif data["choices"]:
            label="miejsce wylotu" if step=="origin" else "cel podróży"
            rows=[[button(o["text"][:55],f"wz:pick:{i}")] for i,o in enumerate(data["choices"])]
            text=f"Które {label} masz na myśli?"
        elif step in ("origin","destination"):
            catalog=WIZARD_ORIGINS if step=="origin" else WIZARD_DESTINATIONS
            field="o" if step=="origin" else "d"
            page=max(0,min(data.get("page",0),(len(catalog)-1)//8))
            title="Skąd lecisz?" if step=="origin" else "Dokąd lecisz?"
            text=f"🧭 Krok {2 if step=='origin' else 3}/{7 if data['trip']=='return' else 6} · {title}\nWybierz miasto, cały kraj lub region. Strona {page+1}/{(len(catalog)+7)//8}."
            rows=grid(catalog[page*8:(page+1)*8],field)
            pages=[]
            if page:pages.append(button("◀️ Poprzednie miejsca",f"wz:page:{page-1}"))
            if (page+1)*8<len(catalog):pages.append(button("Więcej miejsc ▶️",f"wz:page:{page+1}"))
            if pages:rows.append(pages)
            rows.append([button("🔎 Inne miejsce","wz:custom")])
        elif step=="when":
            text=f"🧭 Krok 4/{7 if data['trip']=='return' else 6} · Kiedy wylot?"
            rows=grid([("Najbliższe 7 dni","7"),("Najbliższe 14 dni","14"),("Najbliższe 30 dni","30"),("Najbliższe 60 dni","60"),("Najbliższe 90 dni","90"),("Przyszły miesiąc","next")],"w")
            rows.append([button("📅 Własne daty","wz:custom")])
        elif step=="trip":
            text="✈️ Krok 1 · W jedną stronę czy tam i z powrotem?"
            nav=[nav[1]]
            rows=grid([("➡️ W jedną stronę","oneway"),("🔁 Tam i z powrotem","return")],"t")
        elif step=="nights":
            text="🧭 Krok 5/7 · Ile nocy na miejscu?"
            rows=grid([(f"{n} nocy",n) for n in WIZARD_NIGHTS],"n",3)
            rows.append([button("✍️ Własny zakres","wz:custom")])
        elif step=="stops":
            text=f"🧭 Krok {6 if data['trip']=='return' else 5}/{7 if data['trip']=='return' else 6} · Przesiadki?"
            rows=grid([("Tylko bezpośrednie","0"),("Maks. 1","1"),("Maks. 2","2"),("Obojętnie","4")],"s")
        elif step=="budget":
            text=f"🧭 Krok {7 if data['trip']=='return' else 6}/{7 if data['trip']=='return' else 6} · Maksymalna cena za osobę (PLN)?"
            rows=[[button("Bez limitu","wz:b:0")]]+grid([(f"{b} zł",b) for b in WIZARD_BUDGETS],"b",3)
            rows.append([button("✍️ Własna kwota","wz:custom")])
        else:
            text="🧭 Podsumowanie\n"+describe_search(wizard_spec(data))+"\n1 dorosły · economy · bez dodatkowego bagażu\n\nSprawdź filtry i naciśnij Szukaj lotów."
            rows=[[button("🔎 Szukaj lotów","wz:go")],
                  [button("✏️ Skąd","wz:ed:origin"),button("✏️ Dokąd","wz:ed:destination"),button("✏️ Kiedy","wz:ed:when")],
                  [button("✏️ Typ/pobyt","wz:ed:trip"),button("✏️ Przesiadki","wz:ed:stops"),button("✏️ Budżet","wz:ed:budget")]]
            nav=[nav[1]]
        token=data.get("token", "")
        for row in rows+[nav]:
            for item in row:
                if token and item.get("callback_data", "").startswith("wz:"):
                    item["callback_data"]="wz:"+token+":"+item["callback_data"][3:]
        reply=Reply((note+"\n\n" if note else "")+text,keyboard(*rows,nav));reply.edit=True
        return reply
    def wizard_start(self,owner,data=None):
        data=data or wizard_new();self.store.wizard_set(owner,data)
        reply=self.wizard_prompt(data);reply.edit=False;return reply
    def wizard_place(self,data,field,code,name):
        if code==data.get("destination" if field=="origin" else "origin"):
            raise ValueError("Wybierz różne miejsca wylotu i przylotu.")
        data[field],data[field+"_name"],data["choices"]=code,name,[]
        data.update(page=0,input=False)
        data["step"]=wizard_next(data,field)
    def wizard_set(self,data,field,value):
        if field in ("origin","destination"):
            names=dict((v,k) for k,v in WIZARD_ORIGINS+WIZARD_DESTINATIONS)
            if value not in names:raise ValueError("Nieznane miejsce.")
            self.wizard_place(data,field,value,names[value].lstrip("🌍🌏 "));return
        if field=="when":
            if value=="next":data["start"],data["end"]=next_month()
            elif value in ("7","14","30","60","90"):data["start"],data["end"],data["days"]=("","",int(value))
            else:raise ValueError("Nieznany termin.")
        elif field=="trip":
            if value not in ("oneway","return"):raise ValueError("Nieznany typ podróży.")
            data["trip"]=value
            if value=="oneway":data["nights_from"]=data["nights_to"]=0
        elif field=="nights":
            m=re.fullmatch(r"\s*(\d{1,2})\s*[-–]\s*(\d{1,2})\s*",value)
            if not m or not 1<=int(m[1])<=int(m[2])<=90:raise ValueError("Pobyt wpisz jako min-max nocy, np. 6-9 (1–90).")
            data["trip"],data["nights_from"],data["nights_to"]="return",int(m[1]),int(m[2])
        elif field=="stops":
            if value not in ("0","1","2","4"):raise ValueError("Nieznana liczba przesiadek.")
            data["stops"]=int(value)
        elif field=="budget":
            value=value.replace(" ","").replace(",",".").lower().removesuffix("zł").removesuffix("pln")
            if value=="0":data["budget"]=""
            else:
                try:amount=Decimal(value)
                except ArithmeticError:raise ValueError("Wpisz kwotę w PLN, np. 1500.") from None
                if not amount.is_finite() or not 0<amount<=1_000_000:raise ValueError("Wpisz kwotę w PLN, np. 1500.")
                data["budget"]=format(amount.normalize(),"f")
        data.update(page=0,input=False,choices=[])
        data["step"]=wizard_next(data,field)
    def wizard_callback(self,owner,value):
        data=self.store.wizard_get(owner)
        token, sep, command = value.partition(":")
        if sep and re.fullmatch(r"[a-f0-9]{12}",token):
            if not data or not secrets.compare_digest(token,data.get("token","")):
                return Reply("Ten przycisk pochodzi ze starszego kroku. Użyj najnowszej wiadomości lub /kreator.")
            value=command
        if value=="new":return self.wizard_start(owner)
        if data is None:return Reply("Kreator wygasł. Zacznij od nowa.",keyboard([button("✈️ Nowe wyszukiwanie","wz:new")]))
        if not (sep and re.fullmatch(r"[a-f0-9]{12}",token)):
            return Reply("Ten przycisk pochodzi ze starszego kreatora. Zacznij od /start.",keyboard([button("✈️ Nowe wyszukiwanie","wz:new")]))
        if data and data.get("version") != 2:return self.wizard_start(owner)
        if value=="edit":
            if data["step"] != "summary":
                return Reply("Najpierw dokończ aktualny kreator albo użyj /kreator.")
            data.update(step="summary",edit=True,choices=[]);return self.wizard_start(owner,data)
        if value=="x":
            self.store.wizard_clear(owner)
            reply=Reply("Anulowano kreator.",keyboard([button("🧭 Nowe wyszukiwanie","wz:new")]));reply.edit=True;return reply
        note=""
        try:
            if value=="custom":
                if data["step"] not in WIZARD_TEXT_STEPS:raise ValueError("W tym kroku wybierz przycisk.")
                data.update(input=True,choices=[],page=0)
            elif value.startswith("page:"):
                if data["step"] not in ("origin","destination") or data.get("input") or data["choices"]:
                    raise ValueError("Nieaktualna strona miejsc.")
                page=int(value[5:]);catalog=WIZARD_ORIGINS if data["step"]=="origin" else WIZARD_DESTINATIONS
                if not 0<=page<=(len(catalog)-1)//8:raise ValueError("Nieprawidłowa strona miejsc.")
                data["page"]=page
            elif value=="back":
                if data.get("input"):data["input"]=False
                elif data["choices"]:data["choices"]=[]
                else:data["step"]=wizard_prev(data) if not data["edit"] else "summary"
                data["page"]=0
            elif value.startswith("ed:"):
                step=value[3:]
                if step not in WIZARD_STEPS[:-1]:raise ValueError("Nieznany krok.")
                if data["step"] != "summary":raise ValueError("Najpierw dokończ aktualny krok.")
                data.update(step=step,edit=True,choices=[],page=0,input=False)
            elif value.startswith("pick:"):
                index=int(value[5:])
                if data["step"] not in ("origin","destination") or not 0<=index<len(data["choices"]):
                    raise ValueError("Nieaktualny wybór miejsca.")
                choice=data["choices"][index]
                self.wizard_place(data,data["step"],choice["value"],choice["text"][:120])
            elif value=="go":
                if data["step"] != "summary":raise ValueError("Najpierw dokończ wszystkie kroki kreatora.")
                spec=wizard_spec(data);result=self.provider.search(spec)
                data["edit"]=True;self.store.wizard_set(owner,data)
                reply=self.results_reply(owner,spec,result)
                reply.markup["inline_keyboard"].insert(1,[button("🔁 Zmień filtry","wz:"+data["token"]+":edit")]);reply.edit=True
                return reply
            else:
                field,_,arg=value.partition(":")
                if field not in WIZARD_FIELDS:raise ValueError("Nieznany przycisk.")
                if WIZARD_FIELDS[field] != data["step"]:raise ValueError("Nieaktualny przycisk. Użyj bieżącego kroku.")
                self.wizard_set(data,WIZARD_FIELDS[field],arg)
        except (ValueError,IndexError,ArithmeticError,ProviderError) as e:note="⚠️ "+str(e)
        if value=="go" and note:
            note="Nie udało się pobrać ofert. Twoje filtry są zachowane. Możesz spróbować później albo otworzyć wyszukiwanie na Flightlist."
            try:
                url=BASE+"?"+urlencode(wizard_spec(data).params())
            except ValueError:url=None
        else:url=None
        self.store.wizard_set(owner,data)
        reply=self.wizard_prompt(data,note)
        if url:reply.markup["inline_keyboard"].insert(1,[{"text":"🌐 Otwórz Flightlist","url":url}])
        return reply
    def wizard_text(self,owner,data,text):
        note="";step=data["step"]
        try:
            if step in ("origin","destination"):
                known=WIZARD_KNOWN.get(norm_place(text))
                resolved,choices=(known,[]) if known else location_choices(self.provider,text)
                if choices:data.update(choices=choices[:6],input=False)
                else:
                    name=("" if known else getattr(self.provider,"place_labels",{}).get(resolved,"")) or text.strip()[:120].capitalize()
                    self.wizard_place(data,step,resolved,name)
                    note=f"✅ {'Skąd' if step=='origin' else 'Dokąd'}: {name}"
            elif step=="when":
                data["start"],data["end"]=parse_date_range(text);data["step"]=wizard_next(data,"when");data["input"]=False
            else:self.wizard_set(data,step,text)
        except (ValueError,ArithmeticError,ProviderError) as e:note="⚠️ "+str(e)
        self.store.wizard_set(owner,data)
        reply=self.wizard_prompt(data,note);reply.edit=False;return reply
    def request_reply(self,owner,intent,spec):
        fields=asdict(spec)
        for field in ("origin","destination"):
            value=fields[field]
            if fields.get(field+"_name"):
                resolved,choices=value,[]
            else:
                resolved,choices=location_choices(self.provider,value)
            if choices:
                pending=NaturalSearch(**fields)
                token=self.store.place_request(owner,pending,intent,field,choices)
                label="miejsce wylotu" if field=="origin" else "cel podróży"
                rows=[[button(o["text"][:55],f"place:{token}:{i}")] for i,o in enumerate(choices)]
                return Reply(f"Doprecyzuj {label}: „{value}”. Które miejsce wybierasz?\nPozostałe filtry zachowam.",keyboard(*rows))
            fields[field]=resolved
            fields[field+"_name"]=fields.get(field+"_name") or getattr(self.provider,"place_labels",{}).get(resolved,"")
        validated=Search(**fields)
        result=self.provider.search(validated)
        if intent=="watch":
            wid=self.store.add(owner,validated,result)
            return self.monitor_reply(wid,f"Zapisano monitor #{wid}. Pierwszy odczyt stanowi bazę.\n"+render(result,validated),result.url,result,validated)
        return self.results_reply(owner,validated,result)
    def monitor_reply(self,wid,text,url=None,result=None,spec=None):
        rows=[[button("🔄 Sprawdź teraz",f"check:{wid}"),button("🗑 Usuń",f"delete:{wid}")],[button("📋 Moje obserwacje","list")]]
        if url:
            if result and result.source=="Kiwi.com":
                visible=[o for o in result.offers if not spec or not spec.budget or Decimal(o.price)<=Decimal(spec.budget)][:5]
                rows[1:1]=[[{"text":f"✈️ Oferta {i} · {o.price} {o.currency}","url":o.url}] for i,o in enumerate(visible,1)]
            else:rows.insert(1,[{"text":"🔎 Otwórz stronę dostawcy","url":url}])
            text=text.replace(url,"").rstrip()
        return Reply(text,keyboard(*rows))
    def results_reply(self,owner,spec,result):
        token=self.store.action(owner,spec)
        rows=[[button("🔔 Obserwuj te loty","watch:"+token)]]
        if result.source=="Kiwi.com":
            visible=[o for o in result.offers if not spec.budget or Decimal(o.price)<=Decimal(spec.budget)][:5]
            rows.extend([{"text":f"✈️ Oferta {i} · {o.price} {o.currency}","url":o.url}] for i,o in enumerate(visible,1))
        else:rows.append([{"text":"🔎 Wszystkie wyniki Flightlist","url":result.url}])
        rows.append([button("📋 Moje obserwacje","list")])
        return Reply(render(result,spec).replace(result.url,"").rstrip(),keyboard(*rows))
    def list_reply(self,owner):
        rows=self.store.watches(owner)
        text="\n\n".join(f"#{r['id']} · "+describe_search(Search(**json.loads(r["spec"]))) for r in rows) or "Brak aktywnych obserwacji. Wyszukaj loty i naciśnij „Obserwuj te loty”."
        keys=[[button(f"🔄 Sprawdź #{r['id']}",f"check:{r['id']}"),button(f"🗑 Usuń #{r['id']}",f"delete:{r['id']}")] for r in rows]
        return Reply(text,keyboard([button("✈️ Nowe wyszukiwanie","wz:new")],*keys))
    def callback(self,owner,data):
        try:
            if data=="list":return self.list_reply(owner)
            action,value=data.split(":",1)
            if action=="wz":return self.wizard_callback(owner,value)
            if action=="place":
                token,index=value.split(":",1)
                request,intent=self.store.choose_place(owner,token,int(index))
                return self.request_reply(owner,intent,request)
            if action=="watch":
                spec=self.store.action_spec(owner,value);result=self.provider.search(spec)
                wid=self.store.add(owner,spec,result)
                return self.monitor_reply(wid,f"🔔 Obserwacja #{wid} jest aktywna. Sprawdzam co godzinę.\n"+describe_search(spec))
            wid=int(value)
            if action=="restore":
                self.store.restore(owner,wid)
                return self.monitor_reply(wid,f"Przywrócono obserwację #{wid}.")
            row=self.store.watch(owner,wid)
            if action=="delete":
                self.store.delete(owner,wid)
                return Reply(f"Wyłączono obserwację #{wid}. Możesz ją przywrócić.",keyboard([button("↩️ Przywróć",f"restore:{wid}"),button("📋 Moje obserwacje","list")]))
            if action=="check":
                spec=Search(**json.loads(row["spec"]))
                result=self.provider.search(spec,refresh=True)
                return self.monitor_reply(wid,f"Obserwacja #{wid} · sprawdzenie na żądanie\n"+render(result,spec),result.url,result,spec)
            raise ValueError("Nieznany przycisk.")
        except (ValueError,ProviderError,ArithmeticError):return Reply("Nie udało się wykonać tej akcji. Przycisk mógł wygasnąć, monitor jest nieaktywny albo źródło chwilowo nie odpowiada. Użyj /lista lub wyszukaj loty ponownie.")
    def handle(self,owner,text):
        try:
            if len(text) > 1000: raise ValueError("Polecenie za długie (maks. 1000 znaków).")
            wizard=self.store.wizard_get(owner)
            if wizard and not text.startswith("/"):
                if wizard.get("version") != 2:return self.wizard_start(owner)
                if wizard.get("input"):
                    return self.wizard_text(owner,wizard,text)
                reply=self.wizard_prompt(wizard,"Wybierz przycisk poniżej. Aby wpisać własną wartość, najpierw wybierz odpowiedni przycisk „Inne” lub „Własne”.")
                reply.edit=False
                return reply
            action,arg=parse_command(text, self.provider)
            if wizard and action in ("search","watch"):
                self.store.wizard_clear(owner)
            if action=="wizard":return self.wizard_start(owner)
            if action=="help":return Reply(HELP,keyboard([button("🧭 Kreator wyszukiwania","wz:new")],[button("📋 Moje obserwacje","list")]))
            if action=="locations":
                return "\n".join(f"{x['value']} — {x['text']} ({x.get('type','?')})" for x in self.provider.locations(arg)) or "Brak lokalizacji."
            if action=="list":return self.list_reply(owner)
            if action=="delete":
                return self.callback(owner,f"delete:{arg}")
            if isinstance(arg,NaturalSearch):return self.request_reply(owner,action,arg)
            result=self.provider.search(arg)
            if action=="watch":
                wid=self.store.add(owner,arg,result)
                return self.monitor_reply(wid,f"Zapisano monitor #{wid}. Pierwszy odczyt stanowi bazę.\n"+render(result,arg),result.url,result,arg)
            return self.results_reply(owner,arg,result)
        except (ValueError,ProviderError,ArithmeticError) as e:return "Nie udało się: "+str(e)

class TelegramRejected(RuntimeError):
    def __init__(self,description=""):super().__init__("Telegram odrzucił żądanie.");self.description=str(description)[:200]

class Telegram:
    def __init__(self,token): self.token=token
    def call(self,method,payload):
        req=Request(f"https://api.telegram.org/bot{self.token}/{method}",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
        try:
            with urlopen(req,timeout=40) as response: result=json.load(response)
        except HTTPError as e:
            # 4xx carries a JSON {"ok":false,"description":...}; the URL is not included.
            try:result=json.load(e)
            except Exception:raise RuntimeError("Błąd transportu Telegram; wiadomości pozostają w kolejce.") from None
        except Exception:
            # Never log exceptions containing the token-bearing URL.
            raise RuntimeError("Błąd transportu Telegram; wiadomości pozostają w kolejce.") from None
        if not isinstance(result,dict) or "ok" not in result or (result.get("ok") and "result" not in result):
            raise RuntimeError("Nieprawidłowa odpowiedź Telegram; ponowienie później.")
        if not result.get("ok"):
            if result.get("error_code")==429:raise RuntimeError("Telegram: limit zapytań; ponowienie później.")
            raise TelegramRejected(result.get("description",""))
        return result["result"]
    def edit(self,owner,message_id,text):
        payload={"chat_id":owner,"message_id":message_id,"text":str(text)[:4000],"link_preview_options":{"is_disabled":True}}
        if getattr(text,"markup",None):payload["reply_markup"]=text.markup
        return self.call("editMessageText",payload)
    def send(self,owner,text):
        # Plain text: untrusted provider names cannot inject Telegram HTML.
        payload={"chat_id":owner,"text":str(text)[:4000],"link_preview_options":{"is_disabled":True}}
        if getattr(text,"markup",None):payload["reply_markup"]=text.markup
        return self.call("sendMessage",payload)

def flush(store,telegram):
    for row in store.db.execute("SELECT * FROM outbox ORDER BY id").fetchall():
        reply=Reply(row["body"],json.loads(row["markup"]) if row["markup"] else None)
        try:
            if row["edit_id"]:
                try:telegram.edit(row["owner"],row["edit_id"],reply)
                except TelegramRejected as e:
                    # Same content clicked twice: nothing to change. Otherwise (deleted or
                    # too old message) fall back to a new message.
                    if "not modified" not in e.description:telegram.send(row["owner"],reply)
            else:telegram.send(row["owner"],reply)
        except TelegramRejected as e:
            # A rejected message would block the queue forever; drop it and log why.
            print(json.dumps({"outbox_dropped":row["id"],"reason":e.description},ensure_ascii=False),flush=True)
        with store.db: store.db.execute("DELETE FROM outbox WHERE id=?",(row["id"],))

def process_updates(bot, updates, allowed, offset, telegram=None):
    """Queue replies and acknowledge only after processing; reject other chats."""
    store=bot.store
    for update in updates:
        update_id=update.get("update_id")
        if not isinstance(update_id,int) or update_id < offset: continue
        callback=update.get("callback_query")
        msg=callback.get("message",{}) if callback else update.get("message",{})
        chat=msg.get("chat",{});owner=str(chat.get("id",""))
        reply=None
        if callback and owner in allowed and chat.get("type")=="private" and str(callback.get("from",{}).get("id",""))==owner:
            if telegram:
                answer={"callback_query_id":callback["id"]}
                if callback.get("data")=="wz:go":answer["text"]="Szukam lotów…"
                try:telegram.call("answerCallbackQuery",answer)
                except RuntimeError:pass
            data=callback.get("data","")
            if isinstance(data,str) and len(data)<=64:reply=bot.callback(owner,data)
            if getattr(reply,"edit",False):reply.edit_id=msg.get("message_id")
        if not callback and owner in allowed and chat.get("type")=="private" and isinstance(msg.get("text"),str):
            reply=bot.handle(owner,msg["text"])
        with store.db:
            if reply is not None:store.enqueue(owner,reply)
            offset=update_id+1
            store.db.execute("INSERT OR REPLACE INTO meta VALUES('offset',?)",(str(offset),))
    return offset

def serve(bot,telegram,allowed,interval):
    store=bot.store
    row=store.db.execute("SELECT value FROM meta WHERE key='offset'").fetchone()
    offset=int(row[0]) if row else 0
    next_scan=time.monotonic()+interval
    while True:
        try:
            flush(store,telegram)
            updates=telegram.call("getUpdates",{"offset":offset,"timeout":20,"allowed_updates":["message","callback_query"]})
            offset=process_updates(bot,updates,allowed,offset,telegram)
            if time.monotonic()>=next_scan:
                for error in store.scan(bot.provider): print(json.dumps(error),flush=True)
                next_scan=time.monotonic()+interval
            flush(store,telegram)
        except (RuntimeError,ProviderError,URLError):
            print("Błąd usług zewnętrznych; ponowienie za 60 sekund.",flush=True);time.sleep(60)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",default="flightbot.sqlite3")
    sub=parser.add_subparsers(dest="mode",required=True)
    c=sub.add_parser("chat");c.add_argument("text");c.add_argument("--fixture")
    sub.add_parser("scan")
    p=sub.add_parser("parse");p.add_argument("file")
    s=sub.add_parser("serve");s.add_argument("--live",action="store_true");s.add_argument("--interval",type=int,default=3600)
    args=parser.parse_args()
    if args.mode=="parse":
        result=parse_results(Path(args.file).read_text())
        print(json.dumps({"count":result.count,"parsed":len(result.offers),"first":asdict(result.offers[0]) if result.offers else None},ensure_ascii=False,indent=2));return
    if args.mode=="serve":
        if not args.live:parser.error("Wysyłanie wymaga jawnego --live; domyślnie nic nie wysyłamy.")
        if not os.environ.get("TELEGRAM_TOKEN") or not os.environ.get("ALLOWED_CHAT_IDS"):
            parser.error("Ustaw TELEGRAM_TOKEN i ALLOWED_CHAT_IDS.")
        if args.interval<900:parser.error("Minimalny odstęp monitorowania: 900 s.")
    from kiwi_provider import KiwiProvider
    provider=KiwiProvider()
    if args.mode=="chat" and args.fixture:
        fixture=Path(args.fixture).read_text()
        provider.search=lambda spec:parse_results(fixture,BASE+"?"+urlencode(spec.params()))
    store=Store(args.db);bot=Bot(provider,store)
    if args.mode=="chat":print(bot.handle("local",args.text))
    elif args.mode=="scan":
        print(json.dumps({"errors":store.scan(provider),"pending":store.db.execute("SELECT count(*) FROM outbox").fetchone()[0]}))
    else:
        if not args.live:parser.error("Wysyłanie wymaga jawnego --live; domyślnie nic nie wysyłamy.")
        token=os.environ.get("TELEGRAM_TOKEN","")
        allowed=set(filter(None,os.environ.get("ALLOWED_CHAT_IDS","").split(",")))
        if not token or not allowed:parser.error("Ustaw TELEGRAM_TOKEN i ALLOWED_CHAT_IDS.")
        if args.interval<900:parser.error("Minimalny odstęp monitorowania: 900 s.")
        serve(bot,Telegram(token),allowed,args.interval)

if __name__=="__main__": main()
