import json, datetime, zoneinfo, os, io, base64, hashlib, urllib.request
from PIL import Image
tz = zoneinfo.ZoneInfo('America/Toronto')
t = lambda ts: datetime.datetime.fromtimestamp(ts, tz).strftime('%-I:%M %p %Z')
LOGODIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),'logos'); os.makedirs(LOGODIR, exist_ok=True)
def build():
    s = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'state.json'))); c = s['cfg']
    return _build(s, c)

def logo(url):
    if not url: return None
    f = LOGODIR + '/' + hashlib.md5(url.encode()).hexdigest() + '.txt'
    if os.path.exists(f): return open(f).read() or None
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        raw = urllib.request.urlopen(req, timeout=15).read()
        im = Image.open(io.BytesIO(raw)).convert('RGBA'); im.thumbnail((256, 256))
        w, h = im.size; m = min(w, h); im = im.crop(((w-m)//2, (h-m)//2, (w-m)//2+m, (h-m)//2+m)).resize((64, 64), Image.LANCZOS)
        b = io.BytesIO(); im.save(b, 'WEBP', quality=72); uri = 'data:image/webp;base64,' + base64.b64encode(b.getvalue()).decode()
    except Exception as e:
        print('logo fail', url, e, file=__import__('sys').stderr); uri = ''
    open(f, 'w').write(uri); return uri or None
def spark(h, n=40):
    if not h: return []
    if len(h) > n: step = (len(h)-1)/(n-1); h = [h[round(i*step)] for i in range(n)]
    return [float('%.6g' % p) for _, p in h]
def _build(s, c):
    code = {'take profit': 'TP', 'stop loss': 'SL', 'trailing stop': 'Trail', 'max hold': 'Time'}
    eq = s['equity']
    if len(eq) > 300:
        st = len(eq)/300; eq = [eq[int(i*st)] for i in range(300)] + [s['equity'][-1]]
    closes = [dict(sym=x['sym'], name=x.get('name') or '', logo=logo(x.get('icon')), held=round((x['closed']-x['opened'])/60), at=t(x['closed']),
                   entry=x['entry_px'], exit=x['exit_px'], cost=round(x['cost'], 2), pnl=round(x['pnl'], 2), pct=round(x['pct'], 1),
                   reason=code.get(x['reason'], x['reason']), url=x['url'], spark=spark(x.get('hist'))) for x in s['closes']]
    pos = []
    for p in s['positions']:
        val = p['qty']*p['last']*(1-c['cost_side'])
        pos.append(dict(addr=p['addr'], qty=p['qty'], sym=p['sym'], name=p.get('name') or '', logo=logo(p.get('icon')), url=p['url'], opened=t(p['opened']),
                        held=round((s['last_poll']-p['opened'])/60), cost=round(p['cost'], 2), value=round(val, 2), pnl=round(val-p['cost'], 2),
                        pct=round((val/p['cost']-1)*100, 1), move=round((p['last']/p['entry_px']-1)*100, 1), entry=p['entry_px'], last=p['last'],
                        spark=spark(p.get('hist'))))
    out = dict(asOf=t(s['last_poll']), asOfDate=datetime.datetime.fromtimestamp(s['last_poll'], tz).strftime('%b %-d'),
               started=t(s['started']), startTs=round(s['started']), lastTs=round(s['last_poll']), polls=s['polls'],
               cash=round(s['cash'], 2), equity=[[a, round(b, 2)] for a, b in eq], closes=closes, positions=pos)
    return out

if __name__ == '__main__':
    out = build(); open('snap2.json','w').write(json.dumps(out, separators=(',',':'))); print(len(json.dumps(out)))
