"""Read-only candle proxy for the dashboard: GeckoTerminal OHLCV + DexScreener pool lookup.

Public market data only (no keys, no wallets, no trading). Everything is cached, GeckoTerminal calls are
spaced to stay under its free limit (~30 req/min), a 429 puts us in cooldown, and on any failure the last
good candles are served marked stale, or {ok: false} with the reason when we never had any.
"""
import json, re, threading, time, urllib.error, urllib.request
from collections import OrderedDict

GT = "https://api.geckoterminal.com/api/v2"
DS = "https://api.dexscreener.com"
UA = {"User-Agent": "memebot-desk-dashboard/1.0 (paper)", "Accept": "application/json"}
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TF = {"1m": ("minute", 1), "5m": ("minute", 5), "15m": ("minute", 15), "1h": ("hour", 1)}
CANDLE_TTL = {"1m": 30, "5m": 60, "15m": 120, "1h": 300}
POOL_TTL = 6 * 3600          # a token's main pool rarely changes
NOPOOL_TTL = 300             # retry unknown tokens every 5 min
FAIL_TTL = 45                # after a failed candle fetch, don't hammer for this long
GT_SPACING = 2.2             # seconds between GeckoTerminal calls (~27/min)
LIMIT = 500
MAX_KEYS = 300

_fetch = None                # tests can swap in a fake: fn(url) -> (status, dict|None, headers)


def _http(url, timeout=8):
    if _fetch: return _fetch(url)
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace")), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, None, dict(e.headers or {})
    except Exception:
        return 0, None, {}


class _GT:
    """Serialises GeckoTerminal calls: fixed spacing plus a cooldown after 429/5xx."""
    def __init__(self):
        self.lock = threading.Lock(); self.next_at = 0.0; self.cool_until = 0.0
    def cooling(self):
        return max(0.0, self.cool_until - time.time())
    def get(self, url, max_wait=6.0):
        with self.lock:
            now = time.time()
            if self.cool_until > now: return 429, None, {}
            wait = self.next_at - now
            if wait > max_wait: return 429, None, {}
            if wait > 0: time.sleep(wait)
            self.next_at = time.time() + GT_SPACING
            st, js, hd = _http(url)
            if st == 429:
                try: ra = float(hd.get("Retry-After") or hd.get("retry-after") or 60)
                except ValueError: ra = 60
                self.cool_until = time.time() + min(max(ra, 15), 300)
            elif st >= 500 or st == 0:
                self.cool_until = time.time() + 10
            return st, js, hd


gt = _GT()
_pools = {}                  # mint -> (t, pool or None)
_candles = OrderedDict()     # (mint, tf) -> {"t", "body", "fail_t", "err"}
_keylocks = {}
_mx = threading.Lock()


def _keylock(k):
    with _mx:
        return _keylocks.setdefault(k, threading.Lock())


def find_pool(mint):
    """Most liquid Solana pool for a mint. DexScreener first (no tight limit), GeckoTerminal as fallback."""
    hit = _pools.get(mint)
    if hit and time.time() - hit[0] < (POOL_TTL if hit[1] else NOPOOL_TTL): return hit[1]
    pool = None
    st, js, _ = _http(f"{DS}/tokens/v1/solana/{mint}")
    if st == 200 and isinstance(js, list):
        best = -1
        for p in js:
            if not isinstance(p, dict) or p.get("chainId") != "solana": continue
            addr = p.get("pairAddress") or ""
            liq = ((p.get("liquidity") or {}).get("usd")) or 0
            if MINT_RE.match(addr) and liq > best: best, pool = liq, addr
    if not pool:
        st, js, _ = gt.get(f"{GT}/networks/solana/tokens/{mint}/pools?page=1")
        if st == 429 and hit: return hit[1]
        if st == 429: return "__cooling__"
        for p in ((js or {}).get("data") or []) if st == 200 else []:
            addr = (p.get("attributes") or {}).get("address") or ""
            if MINT_RE.match(addr): pool = addr; break
    if pool or st in (200, 404): _pools[mint] = (time.time(), pool)
    return pool


def _remember(key, entry):
    _candles[key] = entry; _candles.move_to_end(key)
    while len(_candles) > MAX_KEYS: _candles.popitem(last=False)


def candles(mint, tf="1m", pool=None):
    """Returns (http_status, dict). Never raises."""
    if not isinstance(mint, str) or not MINT_RE.match(mint): return 400, {"ok": False, "err": "bad_mint"}
    if tf not in TF: return 400, {"ok": False, "err": "bad_tf", "tfs": list(TF)}
    if pool is not None and not MINT_RE.match(pool): return 400, {"ok": False, "err": "bad_pool"}
    key = (mint, tf); ttl = CANDLE_TTL[tf]
    e = _candles.get(key)
    if e and e.get("body") and time.time() - e["t"] < ttl: return 200, e["body"]
    with _keylock(key):
        e = _candles.get(key)  # another thread may have just refreshed it
        if e and e.get("body") and time.time() - e["t"] < ttl: return 200, e["body"]
        if e and time.time() - e.get("fail_t", 0) < FAIL_TTL: return _stale(e, e.get("err") or "retry_later")
        try:
            return _refresh(key, mint, tf, pool, e)
        except Exception as ex:  # defensive: the dashboard must never 500 because of candles
            e = e or {}; e.update(fail_t=time.time(), err="error"); _remember(key, e)
            return _stale(e, "error:" + type(ex).__name__)


def _stale(e, err):
    if e and e.get("body"):
        b = dict(e["body"]); b.update(stale=True, err=err, age=round(time.time() - e["t"]))
        if gt.cooling(): b["retryIn"] = round(gt.cooling())
        return 200, b
    out = {"ok": False, "err": err}
    if gt.cooling(): out["retryIn"] = round(gt.cooling())
    return (503 if err.startswith("rate") or err == "retry_later" else 200), out


def _refresh(key, mint, tf, pool, e):
    p = pool or find_pool(mint)
    if p == "__cooling__":
        e = e or {}; e.update(fail_t=time.time(), err="rate_limited"); _remember(key, e); return _stale(e, "rate_limited")
    if not p:
        e = e or {}; e.update(fail_t=time.time(), err="no_pool"); _remember(key, e); return _stale(e, "no_pool")
    name, agg = TF[tf]
    st, js, _ = gt.get(f"{GT}/networks/solana/pools/{p}/ohlcv/{name}?aggregate={agg}&limit={LIMIT}&currency=usd&token={mint}")
    if st != 200 or not isinstance(js, dict):
        err = "rate_limited" if st == 429 else ("no_pool" if st == 404 else f"upstream_{st or 'down'}")
        if st == 404: _pools.pop(mint, None)
        e = e or {}; e.update(fail_t=time.time(), err=err); _remember(key, e); return _stale(e, err)
    rows = (((js.get("data") or {}).get("attributes") or {}).get("ohlcv_list")) or []
    seen = {}
    for r in rows:
        try:
            t, o, h, l, c = int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])
            v = float(r[5]) if len(r) > 5 and r[5] is not None else 0.0
        except (TypeError, ValueError, IndexError):
            continue
        if min(o, h, l, c) <= 0: continue
        seen[t] = [t, o, h, l, c, round(v, 2)]
    out = [seen[t] for t in sorted(seen)]
    body = {"ok": True, "mint": mint, "pool": p, "tf": tf, "src": "geckoterminal", "candles": out,
            "fetchedAt": int(time.time()), "stale": False}
    _remember(key, {"t": time.time(), "body": body})
    return 200, body
