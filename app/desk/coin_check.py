# coin_check.py · facts only, no model (DexScreener token-pairs/v1). pump.fun ATH check: frontend-api-v3.pump.fun is behind a
# Cloudflare challenge from this box, so it is attempted and reported as unavailable rather than guessed.
import time, json, urllib.request
from config import *
def _get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read())
def coin_facts(mint):
    pairs = _get(f"https://api.dexscreener.com/token-pairs/v1/solana/{mint}") or []
    liq = sum((p.get("liquidity") or {}).get("usd", 0) or 0 for p in pairs)
    vol = sum((p.get("volume") or {}).get("h24", 0) or 0 for p in pairs)
    born = min((p.get("pairCreatedAt") or 9e15) for p in pairs) / 1000 if pairs else time.time()
    best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0) or 0) if pairs else {}
    f = {"liq": liq, "vol24": vol, "age_s": time.time() - born, "px": float(best.get("priceUsd") or 0),
         "sym": (best.get("baseToken") or {}).get("symbol", "?"), "name": (best.get("baseToken") or {}).get("name", ""),
         "icon": (best.get("info") or {}).get("imageUrl", ""), "url": best.get("url", ""), "mcap": best.get("marketCap") or best.get("fdv") or 0}
    if mint.endswith("pump"):
        try:
            c = _get(f"https://frontend-api-v3.pump.fun/coins-v2/{mint}")
            f["ath_mcap"] = c.get("ath_market_cap")
        except Exception: f["ath_mcap"] = None   # blocked; not used
    return f
def coin_verdict(f):
    if not f["px"]:                     return "SKIP", "no DexScreener pool"
    if f["age_s"] < MIN_COIN_AGE_S:     return "SKIP", f"coin {int(f['age_s'])}s old, launch/sandwich zone"
    if f["liq"] < MIN_LIQ_USD:          return "SKIP", f"liquidity ${f['liq']:,.0f} < ${MIN_LIQ_USD:,}"
    if f["vol24"] < MIN_VOL24_USD:      return "SKIP", f"24h volume ${f['vol24']:,.0f} < ${MIN_VOL24_USD:,}"
    a = f.get("ath_mcap")
    if a and f["mcap"] and f["age_s"] > 3600 and f["mcap"] < PUMP_ATH_WATCH * a: return "SKIP", f"at {f['mcap']/a*100:.0f}% of pump.fun ATH"
    return "OK", "pool is real"
