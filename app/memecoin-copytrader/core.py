#!/usr/bin/env python3
"""Shared helpers for the copy-trader (v2): Helius/public RPC with credit metering, swap parsing,
prices (DexScreener / Jupiter), Jupiter quotes, token safety checks.  Stdlib only.
Secrets: the Helius key is read from .env and redacted from every log line; nothing here touches a private key."""
import argparse, csv, fcntl, json, os, sqlite3, statistics, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "paper.db")
CFG_PATH = os.path.join(HERE, "config.json")
WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
QUOTES = {WSOL, USDC, USDT}
UA = {"User-Agent": "Mozilla/5.0 paper-copytrader", "Content-Type": "application/json"}

# ---------------------------------------------------------------- utils
def load_env(path=os.path.join(HERE, ".env")):
    """Read KEY=VALUE lines from .env into a dict (values are never printed)."""
    out = {}
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out
ENV = load_env()
HELIUS_KEY = os.environ.get("HELIUS_API_KEY") or ENV.get("HELIUS_API_KEY", "")
def redact(x):
    x = str(x)
    return x.replace(HELIUS_KEY, "<HELIUS_KEY>") if HELIUS_KEY else x

def now_ts(): return int(time.time())
def fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"
def log(*a): print(f"[{datetime.now().strftime('%m-%d %H:%M:%S')}]", *[redact(x) for x in a], flush=True)

def http_json(url, payload=None, timeout=25, retries=4):
    data = json.dumps(payload).encode() if payload is not None else None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < retries - 1:
                time.sleep(2 * (i + 1) ** 2); continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if i < retries - 1: time.sleep(2 * (i + 1)); continue
            raise

HELIUS_RPC = "https://mainnet.helius-rpc.com/?api-key="
HELIUS_API = "https://api-mainnet.helius-rpc.com"
CREDIT_COST = {"getTransactionsForAddress": 10}     # everything else on Helius RPC = 1 credit

class RPC:
    """Helius RPC as primary (if HELIUS_API_KEY is set and use_helius), free public endpoints as fallback.
    Counts Helius credits used (self.credits)."""
    def __init__(self, urls, min_interval, use_helius=True, helius_interval=0.12):
        self.public = urls if isinstance(urls, list) else [urls]
        self.helius = HELIUS_RPC + HELIUS_KEY if (use_helius and HELIUS_KEY) else None
        self.intervals = {u: min_interval for u in self.public}
        if self.helius: self.intervals[self.helius] = helius_interval   # free plan: 10 req/s
        self._last = {u: 0.0 for u in self.intervals}
        self._i, self.calls, self.credits, self.helius_fail, self.fallback_calls = 0, 0, 0, 0, 0
    def _post(self, url, method, params):
        wait = self.intervals[url] - (time.time() - self._last[url])
        if wait > 0: time.sleep(wait)
        self._last[url] = time.time(); self.calls += 1
        r = http_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, retries=2)
        if "error" in r: raise RuntimeError(f"RPC {method}: {r['error']}")
        return r["result"]
    def helius_only(self, method, params, credits):
        if not self.helius: raise RuntimeError("no Helius key")
        res = self._post(self.helius, method, params); self.credits += credits
        return res
    def call(self, method, params):
        if self.helius and self.helius_fail < 5:
            try:
                res = self._post(self.helius, method, params)
                self.credits += CREDIT_COST.get(method, 1)
                return res
            except Exception as e:
                self.helius_fail += 1
                log(f"helius {method} failed ({redact(e)}); falling back to public RPC")
        last = None
        for t in range(2 * len(self.public)):
            url = self.public[self._i % len(self.public)]; self._i += 1
            try:
                self.fallback_calls += 1
                return self._post(url, method, params)
            except Exception as e:
                last = e; time.sleep(1 + (t if "429" in str(e) or "rate" in str(e).lower() else 0))
        raise RuntimeError(redact(last))
    def txs_for_address(self, addr, gte_ts, want_sigs, max_pages=5):
        """Helius getTransactionsForAddress: successful full txs since gte_ts, oldest first.
        10 credits per 100 returned (cheaper than 1 credit/getTransaction when >10 txs)."""
        out, token = {}, None
        for _ in range(max_pages):
            p = {"transactionDetails": "full", "encoding": "jsonParsed", "maxSupportedTransactionVersion": 1,
                 "sortOrder": "asc", "limit": 100, "commitment": "confirmed",
                 "filters": {"status": "succeeded", "blockTime": {"gte": int(gte_ts)}}}
            if token: p["paginationToken"] = token
            res = self.helius_only("getTransactionsForAddress", [addr, p], 0)
            data = res.get("data") or []
            self.credits += max(10, 10 * -(-len(data) // 100))
            for tx in data:
                sig = tx["transaction"]["signatures"][0]
                if sig in want_sigs: out[sig] = tx
            token = res.get("paginationToken")
            if not token or not data or len(out) >= len(want_sigs): break
        return out
    def enhanced_swaps(self, addr, limit=100, before=None):
        """Helius Enhanced Transactions API, type=SWAP (100 credits per call). Used by `analyze --helius`."""
        if not HELIUS_KEY: raise RuntimeError("no Helius key")
        u = f"{HELIUS_API}/v0/addresses/{addr}/transactions?api-key={HELIUS_KEY}&type=SWAP&limit={limit}"
        if before: u += f"&before-signature={before}"
        try:
            res = http_json(u, retries=3)
        except Exception as e:
            raise RuntimeError(redact(e))
        self.credits += 100
        return res
    def signatures(self, addr, limit=25, until=None, before=None):
        p = {"limit": limit}
        if until: p["until"] = until
        if before: p["before"] = before
        return self.call("getSignaturesForAddress", [addr, p])
    def ok_signatures(self, addr, want, until=None, min_ts=0, max_pages=10):
        """Newest-first list of up to `want` NON-FAILED signatures. Leader wallets are flooded with failed
        spam txs (address-poisoning bots), so we page 1000 sigs at a time and drop err ones cheaply.
        Returns (ok_sigs, newest_sig_seen)."""
        ok, before, newest = [], None, None
        for _ in range(max_pages):
            page = self.signatures(addr, limit=1000, until=until, before=before)
            if not page: break
            newest = newest or page[0]
            for s in page:
                if min_ts and (s.get("blockTime") or 0) < min_ts: return ok, newest
                if not s.get("err"): ok.append(s)
                if len(ok) >= want: return ok, newest
            if len(page) < 1000: break
            before = page[-1]["signature"]
        return ok, newest
    def tx(self, sig):
        return self.call("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1,
                                                  "commitment": "confirmed"}])

# ---------------------------------------------------------------- swap parsing
def parse_swaps(tx, wallet):
    """Return list of swap dicts (side buy/sell) for `wallet` in a parsed tx, using balance deltas."""
    if not tx or not tx.get("meta") or tx["meta"].get("err"): return []
    meta = tx["meta"]
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
    if wallet not in keys: return []
    i = keys.index(wallet)
    sol_delta = (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9
    if i == 0: sol_delta += meta.get("fee", 0) / 1e9   # ignore network fee for price purposes
    def bal(lst):
        out = {}
        for b in lst or []:
            if b.get("owner") != wallet: continue
            amt = float(b["uiTokenAmount"].get("uiAmountString") or 0)
            out[b["mint"]] = out.get(b["mint"], 0.0) + amt
        return out
    pre, post = bal(meta.get("preTokenBalances")), bal(meta.get("postTokenBalances"))
    deltas = {m: post.get(m, 0.0) - pre.get(m, 0.0) for m in set(pre) | set(post)}
    sol_delta += deltas.pop(WSOL, 0.0)
    usd_delta = deltas.pop(USDC, 0.0) + deltas.pop(USDT, 0.0)
    moved = {m: d for m, d in deltas.items() if abs(d) > 1e-12}
    if len(moved) != 1: return []          # token->token routes / airdrops / multi-mint: skip
    mint, d = next(iter(moved.items()))
    ts = tx.get("blockTime") or 0
    sig = tx["transaction"]["signatures"][0]
    if d > 0 and (sol_delta < -1e-6 or usd_delta < -1e-6):
        side = "buy"
    elif d < 0 and (sol_delta > 1e-6 or usd_delta > 1e-6):
        side = "sell"
    else:
        return []                          # transfer, not a swap
    return [{"sig": sig, "ts": ts, "mint": mint, "side": side, "token_amount": abs(d),
             "sol_amount": abs(sol_delta), "usd_quote_amount": abs(usd_delta),
             "pre_token": pre.get(mint, 0.0), "post_token": post.get(mint, 0.0)}]

def parse_enhanced_swap(x, wallet):
    """Swap dict from a Helius Enhanced Transactions record (only swaps the wallet itself signed/paid).
    Pre/post token balances are not in this format, so pre_token/post_token are 0 (= treated as full exit)."""
    if x.get("transactionError") or x.get("feePayer") != wallet: return []
    sol_delta, deltas = 0.0, {}
    for a in x.get("accountData", []):
        if a.get("account") == wallet: sol_delta += a.get("nativeBalanceChange", 0) / 1e9
        for c in a.get("tokenBalanceChanges", []):
            if c.get("userAccount") != wallet: continue
            raw = c["rawTokenAmount"]; amt = int(raw["tokenAmount"]) / 10 ** int(raw["decimals"])
            deltas[c["mint"]] = deltas.get(c["mint"], 0.0) + amt
    sol_delta += x.get("fee", 0) / 1e9
    sol_delta += deltas.pop(WSOL, 0.0)
    usd_delta = deltas.pop(USDC, 0.0) + deltas.pop(USDT, 0.0)
    moved = {m: d for m, d in deltas.items() if abs(d) > 1e-12}
    if len(moved) != 1: return []
    mint, d = next(iter(moved.items()))
    if d > 0 and (sol_delta < -1e-6 or usd_delta < -1e-6): side = "buy"
    elif d < 0 and (sol_delta > 1e-6 or usd_delta > 1e-6): side = "sell"
    else: return []
    return [{"sig": x["signature"], "ts": x.get("timestamp") or 0, "mint": mint, "side": side, "token_amount": abs(d),
             "sol_amount": abs(sol_delta), "usd_quote_amount": abs(usd_delta), "pre_token": 0.0, "post_token": 0.0}]

# ---------------------------------------------------------------- prices
def dexscreener_info(mints):
    """mint -> {price_usd, liquidity_usd, mcap, symbol, pair_url, dex} using the most liquid pair."""
    out = {}
    mints = list(dict.fromkeys(mints))
    for j in range(0, len(mints), 30):
        chunk = mints[j:j + 30]
        try:
            pairs = http_json("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(chunk))
        except Exception as e:
            log("dexscreener error", e); pairs = []
        for p in pairs or []:
            m = p.get("baseToken", {}).get("address")
            if m not in chunk or not p.get("priceUsd"): continue
            liq = (p.get("liquidity") or {}).get("usd") or 0.0
            if m not in out or liq > out[m]["liquidity_usd"]:
                out[m] = {"price_usd": float(p["priceUsd"]), "liquidity_usd": float(liq),
                          "mcap": float(p.get("marketCap") or p.get("fdv") or 0),
                          "symbol": p["baseToken"].get("symbol", "?"), "pair_url": p.get("url", ""),
                          "dex": p.get("dexId", ""), "source": "dexscreener",
                          "pair_created_ms": int(p.get("pairCreatedAt") or 0)}
    missing = [m for m in mints if m not in out]
    if missing:   # fallback: Jupiter price API v3 (free, no key) - has price + liquidity
        for j in range(0, len(missing), 50):
            try:
                r = http_json("https://lite-api.jup.ag/price/v3?ids=" + ",".join(missing[j:j + 50]))
            except Exception as e:
                log("jupiter error", e); r = {}
            for m, v in (r or {}).items():
                if v and v.get("usdPrice"):
                    out[m] = {"price_usd": float(v["usdPrice"]), "liquidity_usd": float(v.get("liquidity") or 0),
                              "mcap": 0.0, "symbol": "?", "pair_url": "", "dex": "", "source": "jupiter",
                              "pair_created_ms": 0}
    return out

def sol_price():
    info = dexscreener_info([WSOL]).get(WSOL)
    return info["price_usd"] if info else 0.0


def history(self, addr, gte_ts, max_pages=10):
    """Helius getTransactionsForAddress, newest first, successful txs since gte_ts (10 credits/100 txs).
    Used by `backfill` to measure leader hold times."""
    out, token = [], None
    for _ in range(max_pages):
        p = {"transactionDetails": "full", "encoding": "jsonParsed", "maxSupportedTransactionVersion": 1,
             "sortOrder": "desc", "limit": 100, "commitment": "confirmed",
             "filters": {"status": "succeeded", "blockTime": {"gte": int(gte_ts)}}}
        if token: p["paginationToken"] = token
        res = self.helius_only("getTransactionsForAddress", [addr, p], 0)
        data = res.get("data") or []
        self.credits += max(10, 10 * -(-len(data) // 100))
        out.extend(data)
        token = res.get("paginationToken")
        if not token or not data: break
    return out, bool(token)
RPC.history = history
del history

# ---------------------------------------------------------------- Jupiter (quotes are free, no key)
JUP_BASE = "https://lite-api.jup.ag/swap/v1"
def jup_quote(input_mint, output_mint, amount_raw, slippage_bps=300):
    """Jupiter route quote. amount_raw in base units of input_mint. Returns dict or None (no route)."""
    u = (f"{JUP_BASE}/quote?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_raw)}"
         f"&slippageBps={int(slippage_bps)}&restrictIntermediateTokens=true")
    try:
        q = http_json(u, retries=2, timeout=15)
    except Exception as e:
        log("jupiter quote error", str(e)[:120]); return None
    if not q or "outAmount" not in q: return None
    return q

_dec_cache = {}
def token_decimals(rpc, mint):
    """Decimals + mint/freeze authority via getAccountInfo jsonParsed (1 Helius credit)."""
    if mint in _dec_cache: return _dec_cache[mint]
    r = rpc.call("getAccountInfo", [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}])
    v = (r or {}).get("value") or {}
    info = (((v.get("data") or {}).get("parsed") or {}).get("info")) or {}
    out = {"decimals": int(info.get("decimals", 0)), "mint_authority": info.get("mintAuthority"),
           "freeze_authority": info.get("freezeAuthority"), "program": v.get("owner")}
    _dec_cache[mint] = out
    return out

def rugcheck(mint):
    """RugCheck public summary (free). Returns dict or None if unavailable."""
    try:
        return http_json(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary", retries=2, timeout=12)
    except Exception as e:
        log("rugcheck unavailable", str(e)[:100]); return None
