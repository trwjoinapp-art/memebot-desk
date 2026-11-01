# safety.py · coin safety facts for the PAPER copier (read-only public data).
#  DexScreener: liquidity / 24h volume / pool age.  Helius RPC: mint & freeze authority, Token-2022 extensions,
#  top-holder concentration (getTokenLargestAccounts, pool/PDA accounts excluded).  pump.fun: ATH market cap.
#  rugcheck.xyz public summary: danger-level risks.
import sys, time, json, urllib.request, threading
sys.path.insert(0, "/workspace/memecoin-copytrader")
import core
from core import http_json
from config import *
from coin_check import coin_facts
URL = core.HELIUS_RPC + core.HELIUS_KEY
SYSTEM = "11111111111111111111111111111111"
BAD_EXT = {"permanentDelegate", "nonTransferable", "transferHook", "defaultAccountState", "pausableConfig"}
RC_IGNORE = {"Low Liquidity", "Low amount of LP Providers", "Large Amount of LP Unlocked", "LP Unlocked", "Mutable metadata"}
_cache, _lock = {}, threading.Lock(); credits = [0]
def rpc(m, p):
    r = http_json(URL, {"jsonrpc": "2.0", "id": 1, "method": m, "params": p}, retries=3); credits[0] += 1
    if "error" in r: raise RuntimeError(str(r["error"])[:150])
    return r["result"]
def chain_facts(mint):
    out = {}
    acc = rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])["value"]
    info = acc["data"]["parsed"]["info"]
    out["mintAuth"] = bool(info.get("mintAuthority")); out["freezeAuth"] = bool(info.get("freezeAuthority"))
    exts = [e.get("extension") for e in info.get("extensions") or []]
    out["token2022"] = acc["owner"] != "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    out["badExt"] = [e for e in exts if e in BAD_EXT]
    for e in info.get("extensions") or []:
        if e.get("extension") == "transferFeeConfig":
            bps = ((e.get("state") or {}).get("newerTransferFee") or {}).get("transferFeeBasisPoints", 0)
            if bps: out["badExt"].append(f"transfer fee {bps/100:.1f}%")
    supply = float(info["supply"]) / 10 ** info["decimals"]
    top = rpc("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])["value"][:20]
    tas = [t["address"] for t in top]
    ta = rpc("getMultipleAccounts", [tas, {"encoding": "jsonParsed"}])["value"] if tas else []
    owners = [((a or {}).get("data") or {}).get("parsed", {}).get("info", {}).get("owner") for a in ta]
    uo = [o for o in dict.fromkeys(owners) if o]
    oa = dict(zip(uo, rpc("getMultipleAccounts", [uo, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}])["value"])) if uo else {}
    wallets = []
    for t, o in zip(top, owners):
        a = oa.get(o)
        if a and a.get("owner") == SYSTEM: wallets.append(float(t.get("uiAmountString") or t.get("uiAmount") or 0))   # real wallet, not pool/PDA
    out["topPct"] = round(max(wallets) / supply * 100, 2) if wallets and supply else 0
    out["top10Pct"] = round(sum(sorted(wallets, reverse=True)[:10]) / supply * 100, 2) if wallets and supply else 0
    return out
def rugcheck(mint):
    try:
        r = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary",
                       headers={"User-Agent": "Mozilla/5.0"}), timeout=8).read())
        return [x["name"] for x in r.get("risks") or [] if x.get("level") == "danger" and x.get("name") not in RC_IGNORE]
    except Exception: return None
def check(mint, max_age=SAFETY_CACHE_S):
    with _lock:
        c = _cache.get(mint)
        if c and time.time() - c[0] < max_age: return c[1]
    f = coin_facts(mint)
    s = dict(liq=round(f["liq"]), vol24=round(f["vol24"]), ageMin=round(f["age_s"] / 60, 1), mcap=round(f["mcap"] or 0), px=f["px"],
             sym=f["sym"], name=f["name"], icon=f["icon"], url=f["url"], athPct=None, fails=[])
    a = f.get("ath_mcap")
    if a and f["mcap"]: s["athPct"] = round(f["mcap"] / a * 100, 1)
    if not f["px"]: s["fails"].append("no DexScreener pool")
    if f["age_s"] < MIN_COIN_AGE_S: s["fails"].append(f"coin {int(f['age_s'])}s old")
    if f["liq"] < MIN_LIQ_USD: s["fails"].append(f"liquidity ${f['liq']:,.0f} < ${MIN_LIQ_USD:,}")
    if f["vol24"] < MIN_VOL24_USD: s["fails"].append(f"24h vol ${f['vol24']:,.0f} < ${MIN_VOL24_USD:,}")
    if s["athPct"] is not None and f["age_s"] > 3600 and s["athPct"] < PUMP_ATH_WATCH * 100: s["fails"].append(f"at {s['athPct']:.0f}% of ATH")
    if not s["fails"]:          # only spend Helius credits on coins that pass the cheap checks
        try:
            cf = chain_facts(mint); s.update(cf)
            if cf["mintAuth"]: s["fails"].append("mint authority still set")
            if cf["freezeAuth"]: s["fails"].append("freeze authority set (honeypot risk)")
            if cf["badExt"]: s["fails"].append("token-2022: " + ", ".join(cf["badExt"]))
            if cf["topPct"] > TOP1_MAX_PCT: s["fails"].append(f"top wallet holds {cf['topPct']:.0f}%")
            if cf["top10Pct"] > TOP10_MAX_PCT: s["fails"].append(f"top 10 wallets hold {cf['top10Pct']:.0f}%")
        except Exception as e: s["fails"].append(f"chain check failed: {str(e)[:50]}")
        rc = rugcheck(mint); s["rugcheck"] = rc
        if rc: s["fails"].append("rugcheck: " + ", ".join(rc[:3]))
    s["ok"] = not s["fails"]
    with _lock: _cache[mint] = (time.time(), s)
    return s
if __name__ == "__main__":
    for m in sys.argv[1:]: print(json.dumps(check(m))); print("credits", credits[0])
