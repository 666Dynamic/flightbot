"""Official public Kiwi MCP flight search; no browser or Flightlist session reuse."""
import hashlib
import json
import time
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_CEILING
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from flightbot import Flightlist, Offer, Result, ProviderError, search_identity

ENDPOINT = 'https://mcp.kiwi.com'
CITY_NAMES = {'WAW':'Warsaw','KRK':'Kraków','GDN':'Gdańsk','KTW':'Katowice','WRO':'Wrocław',
              'POZ':'Poznań','LCJ':'Łódź','SZZ':'Szczecin','RZE':'Rzeszów','LUZ':'Lublin',
              'BER':'Berlin','PRG':'Prague','VIE':'Vienna','MNL':'Manila','BKK':'Bangkok',
              'HAM':'Hamburg','LON':'London, United Kingdom','PAR':'Paris','ROM':'Rome','NYC':'New York'}

class KiwiProvider(Flightlist):
    # Location suggestions retain Flightlist's independent, working location endpoint.
    def __init__(self, cache_seconds=300):
        super().__init__(cache_seconds)
        self.initialized=False
        self.sequence=0
        self.session=None

    def rpc(self, method, params):
        self.sequence+=1
        payload={'jsonrpc':'2.0','id':self.sequence,'method':method,'params':params}
        headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
        if self.session:headers['Mcp-Session-Id']=self.session
        req=Request(ENDPOINT,data=json.dumps(payload).encode(),headers=headers)
        try:
            with urlopen(req,timeout=100) as response:
                if urlparse(response.url).hostname!='mcp.kiwi.com':raise ProviderError('Nieoczekiwane przekierowanie Kiwi.')
                if response.getheader('Mcp-Session-Id'):self.session=response.getheader('Mcp-Session-Id')
                # Read one complete RPC message, rather than waiting for an SSE connection to close.
                if 'text/event-stream' in response.getheader('Content-Type',''):
                    chunks=[];size=0;message=None
                    for raw in response:
                        size+=len(raw)
                        if size>5_000_000:raise ProviderError('Odpowiedź Kiwi zbyt duża.')
                        line=raw.decode('utf-8').rstrip('\r\n')
                        if line.startswith('data:'):chunks.append(line[5:].lstrip(' '))
                        elif not line and chunks:
                            candidate=json.loads('\n'.join(chunks));chunks=[]
                            if candidate.get('id')==self.sequence:message=candidate;break
                    if message is None:raise ProviderError('Kiwi nie zwróciło odpowiedzi wyszukiwania.')
                else:
                    body=response.read(5_000_001)
                    if len(body)>5_000_000:raise ProviderError('Odpowiedź Kiwi zbyt duża.')
                    message=json.loads(body)
        except HTTPError as e:
            raise ProviderError(f'Kiwi HTTP {e.code}. Spróbuj ponownie później.') from None
        except (OSError,URLError,ValueError,TypeError,AttributeError):
            raise ProviderError('Nie udało się odczytać odpowiedzi Kiwi. Spróbuj ponownie później.') from None
        if not isinstance(message,dict) or message.get('id')!=self.sequence or 'error' in message or not isinstance(message.get('result'),dict):
            raise ProviderError('Kiwi odrzuciło żądanie wyszukiwania.')
        return message['result']

    def arguments(self,spec):
        p=spec.params()
        def place(code,label):
            if code.startswith('city:'):
                return CITY_NAMES.get(code[5:]) or label.split('(')[0].split(',')[0].strip() or code[5:]
            return code
        fmt=lambda s:date.fromisoformat(s).strftime('%d/%m/%Y')
        args={'flyFrom':place(spec.origin,spec.origin_name),'flyTo':place(spec.destination,spec.destination_name),
              'departureDate':fmt(p['depart_from']),'departureDateTo':fmt(p['depart_to']),
              'adults':1,'children':0,'infants':0,'cabinClass':'M','currency':spec.currency,'locale':'en',
              'max_sector_stopovers':spec.stops,'allow_self_transfer':False,'sort':'price',
              'adults_hand_bags':[0],'adults_hold_bags':[0]}
        if spec.budget:args['price_to']=int(Decimal(spec.budget).to_integral_value(rounding=ROUND_CEILING))
        if spec.nights_from:
            args.update(returnDate=fmt(p['return_from']),returnDateTo=fmt(p['return_to']),
                        nights_in_dst_from=spec.nights_from,nights_in_dst_to=spec.nights_to)
        return args

    def search(self,spec,refresh=False):
        args=self.arguments(spec);key=('kiwi',json.dumps(args,sort_keys=True))
        cached=self.cache.get(key)
        if not refresh and cached and time.monotonic()-cached[0]<self.cache_seconds:return cached[1]
        if not self.initialized:
            self.rpc('initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'flightbot','version':'0.3'}})
            self.initialized=True
        delay=2-(time.monotonic()-self.last_request)
        if delay>0:time.sleep(delay)
        self.last_request=time.monotonic()
        response=self.rpc('tools/call',{'name':'search-flight','arguments':args})
        if response.get('isError'):raise ProviderError('Kiwi nie mogło wykonać wyszukiwania. Sprawdź miejsca i daty.')
        data=response.get('structuredContent')
        if not isinstance(data,dict):
            try:data=next(json.loads(x['text']) for x in response.get('content',[]) if x.get('type')=='text' and x.get('text','').lstrip().startswith('{'))
            except (ValueError,KeyError,StopIteration,TypeError):raise ProviderError('Nieprawidłowy format ofert Kiwi.') from None
        result=parse_kiwi(data,spec)
        if len(self.cache)>=128:self.cache.pop(next(iter(self.cache)))
        self.cache[key]=(time.monotonic(),result)
        return result


def parse_kiwi(data,spec):
    if data.get('error'):raise ProviderError('Kiwi zgłosiło błąd wyszukiwania. Sprawdź miejsca i daty.')
    if data.get('currency')!=spec.currency:raise ProviderError('Kiwi zwróciło inną walutę niż zamówiona.')
    items=data.get('itineraries')
    if not isinstance(items,list) or data.get('resultsCount')!=len(items):raise ProviderError('Niespójna lista ofert Kiwi.')
    p=spec.params();offers={}
    try:
        for item in items:
            amount=Decimal(str(item['price']))
            if not amount.is_finite() or amount<=0:raise ValueError('price')
            url=item['bookingUrl'];parsed=urlparse(url)
            if parsed.scheme!='https' or parsed.hostname not in ('kiwi.com','www.kiwi.com'):raise ValueError('booking URL')
            outbound=item['outbound'];inbound=item.get('inbound')
            if bool(inbound)!=bool(spec.nights_from):raise ValueError('trip')
            depart=date.fromisoformat(outbound['departureTime'][:10])
            if not date.fromisoformat(p['depart_from'])<=depart<=date.fromisoformat(p['depart_to']):raise ValueError('dates')
            if inbound:
                # Nights at destination start on arrival, not departure, of the outbound sector.
                nights=(date.fromisoformat(inbound['departureTime'][:10])-date.fromisoformat(outbound['arrivalTime'][:10])).days
                if not spec.nights_from<=nights<=spec.nights_to:raise ValueError('stay')
                if not date.fromisoformat(p['return_from'])<=date.fromisoformat(inbound['departureTime'][:10])<=date.fromisoformat(p['return_to']):raise ValueError('return dates')
            identity=[];summaries=[]
            for leg in [outbound]+([inbound] if inbound else []):
                segments=leg['segments']
                if not segments or len(segments)-1>spec.stops or leg['stops']!=len(segments)-1:raise ValueError('stops')
                # Enforce exact airports when requested. City and country resolution is provider-side.
                expected_from=spec.origin if leg is outbound else spec.destination
                expected_to=spec.destination if leg is outbound else spec.origin
                if len(expected_from)==3 and segments[0]['from']!=expected_from:raise ValueError('origin')
                if len(expected_to)==3 and segments[-1]['to']!=expected_to:raise ValueError('destination')
                carriers=', '.join(dict.fromkeys(s['carrierName'] for s in segments))
                departure=datetime.fromisoformat(leg['departureTime']);arrival=datetime.fromisoformat(leg['arrivalTime'])
                duration=int(leg['durationSeconds']);h,m=divmod(duration//60,60)
                if duration<=0:raise ValueError('duration')
                summaries.append(f"{carriers} · {departure:%d.%m.%Y %H:%M} → {arrival:%d.%m.%Y %H:%M} · {h}h {m:02d}m · "+
                    f"{segments[0]['fromCity']} ({segments[0]['from']}) → {segments[-1]['toCity']} ({segments[-1]['to']}) · "+
                    ('bez przesiadek' if not leg['stops'] else f"{leg['stops']} przesiadka/i"))
                identity.append([(s['from'],s['to'],s['departureTime'],s['arrivalTime'],s.get('carrier'),s.get('flightNumber')) for s in segments])
            summary=summaries[0]+('\nPowrót: '+summaries[1] if inbound else '')
            key='kiwi:'+hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:24]
            offer=Offer(key,format(amount.normalize(),"f"),spec.currency,summary,url)
            if key not in offers or amount<Decimal(offers[key].price):offers[key]=offer
    except (ValueError,KeyError,TypeError,ArithmeticError,IndexError):
        raise ProviderError('Oferta Kiwi nie spełnia filtrów lub ma nieprawidłowy format; nie zmieniam obserwacji.') from None
    ordered=sorted(offers.values(),key=lambda o:Decimal(o.price))
    # Public MCP returns a curated set, not the entire inventory.
    return Result(ordered,len(ordered),ordered[0].url if ordered else 'https://www.kiwi.com/',datetime.now(timezone.utc).isoformat(),True,source='Kiwi.com')
