# copier2.py · PAPER copy desk v2. Reads public chain data only (Helius websocket + RPC) and DexScreener prices.
# No wallet, no private key, no signing, no transactions sent. Every trade is simulated in desk_state.json.
import sys, os, json, time, threading, queue, asyncio, traceback, statistics, datetime
sys.path.insert(0, "/workspace/memecoin-copytrader")
import core
from core import http_json, parse_swaps, dexscreener_info
import config as C
from config import *
import safety
assert PAPER is True
URL = core.HELIUS_RPC + core.HELIUS_KEY; WSS = "wss://mainnet.helius-rpc.com/?api-key=" + core.HELIUS_KEY
HERE = os.path.dirname(os.path.abspath(__file__)); SF = os.path.join(HERE, "desk_state.json")
L = threading.RLock(); Q = queue.Queue(); SEEN = set(); POLL_NOW = threading.Event(); WS_KICK = [False]
VENUE = {'6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P': 'pump.fun bonding curve', 'LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj': 'Raydium LaunchLab bonding curve',
         'dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN': 'Meteora DBC bonding curve', 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA': 'PumpSwap',
         'JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4': 'Jupiter', '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8': 'Raydium AMM', 'CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C': 'Raydium CPMM',
         'LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo': 'Meteora DLMM', 'cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG': 'Meteora DAMM v2'}
def venue(tx):
    ins = [i.get('programId') for i in tx['transaction']['message']['instructions']]
    for g in tx['meta'].get('innerInstructions') or []: ins += [i.get('programId') for i in g['instructions']]
    v = [VENUE[p] for p in ins if p in VENUE]
    curve = [x for x in v if 'bonding' in x]
    return (curve or [x for x in v if x != 'Jupiter'] or v or ['unknown venue'])[0]
_JT = {}
def jup_token(mint):
    """Symbol/icon/mcap/liquidity/holders from Jupiter's token API (covers bonding-curve coins DexScreener doesn't list)."""
    if mint in _JT and time.time() - _JT[mint][0] < 120: return _JT[mint][1]
    try:
        r = http_json(f"https://lite-api.jup.ag/tokens/v2/search?query={mint}", timeout=6, retries=1)
        t = next((x for x in r or [] if x.get("id") == mint), None) or {}
    except Exception: t = {}
    _JT[mint] = (time.time(), t); return t
def feed(s, **kw):
    with L: s.setdefault('feed', []).insert(0, dict(ts=int(time.time()), **kw)); del s['feed'][200:]
try: C_SOL = [core.sol_price() or 150.0]
except Exception: C_SOL = [150.0]
def log(*a): core.log(*a)
def rpc(s, m, p):
    r = http_json(URL, {"jsonrpc": "2.0", "id": 1, "method": m, "params": p}, retries=3)
    s["credits"] += 1
    if "error" in r: raise RuntimeError(str(r["error"])[:150])
    return r["result"]
def cfg():
    return dict(ticket=LIVE_TICKET_USD, max_coin=MAX_PER_COIN, slip=SLIP_FEE_SIDE, delay=EXEC_DELAY_S, fillSource=FILL_SOURCE, prioFeeSol=PRIO_FEE_SOL, copyAdds=COPY_ADDS, poll=BACKUP_POLL_SEC,
                stopLoss=STOP_LOSS, tp1X=TP1_X, tp1Frac=TP1_FRAC, trail=TRAIL, maxHoldMin=MAX_HOLD_MIN, maxOpen=MAX_OPEN,
                dayLoss=DAY_LOSS_PCT, consensusN=CONSENSUS_N, consensusWinS=CONSENSUS_WIN_S, boostX=BOOST_X, exitMode=EXIT_MODE, mirror=MIRROR, mode=("MIRROR" if MIRROR else "FILTERED"), ticketCore=MIRROR_TICKET_CORE, ticketScout=MIRROR_TICKET_SCOUT, maxHoldH=MIRROR_MAX_HOLD_H, noQuoteMin=MIRROR_NOQUOTE_MIN,
                tp_mult=TP_MULT, tp_frac=TP_FRAC, trail_pct=TRAIL_PCT)
def save(s):
    with L:
        s["safety_credits"] = safety.credits[0]
        json.dump(s, open(SF + ".tmp", "w")); os.replace(SF + ".tmp", SF)
def shortlist_wallets():
    sl = json.load(open(os.path.join(HERE, "shortlist3.json" if os.path.exists(os.path.join(HERE, "shortlist3.json")) else "shortlist2.json")))
    ws = [dict(name=w["name"], address=w["address"], tier="core", last_sig=None) for w in sl["shortlist"]] + \
         [dict(name=w["name"], address=w["address"], tier="scout", last_sig=None) for w in sl["scouts"]]
    return [w for w in ws if w["address"] not in DROP_WALLETS]
def sync_wallets(s):
    """Persisted state used to ignore shortlist changes. Rebuild the watch list from the shortlist on every start,
    keeping last_sig/tstats for wallets that stay; open positions keep the name they were opened under."""
    old = {w["address"]: w for w in s.get("wallets", [])}; ws = shortlist_wallets()
    for w in ws:
        if w["address"] in old: w["last_sig"] = old[w["address"]].get("last_sig")
        s.setdefault("tstats", {}).setdefault(w["address"], dict(signals=0, copied=0, skipped=0, pnl=0.0, last=0))
    added = [w["name"] for w in ws if w["address"] not in old]; removed = [w["name"] for a, w in old.items() if a not in {x["address"] for x in ws}]
    if added or removed: log("WALLET SYNC from shortlist: +", added, "-", removed)
    s["wallets"] = ws
    return s
def load():
    if os.path.exists(SF):
        s = json.load(open(SF))
        if s.get("version") == 2 and s.get("desk") == 3: s["cfg"] = cfg(); return sync_wallets(s)
    ws = shortlist_wallets()
    now = int(time.time())
    return dict(version=2, desk=3, cfg=cfg(), started=now, cash=START_USD, positions=[], closes=[], skipped=[], equity=[[now, START_USD]],
                polls=0, credits=0, signals=0, last_poll=0, wallets=ws, recent={}, day=dict(key="", start_eq=START_USD, halted=False, why=""),
                detect=dict(mode=DETECT, ws=False, last_event=0, lags=[]),
                tstats={w["address"]: dict(signals=0, copied=0, skipped=0, pnl=0.0, last=0) for w in ws})
WSOL = "So11111111111111111111111111111111111111112"; _DEC = {}
def decimals(s, mint):
    if mint not in _DEC:
        r = rpc(s, "getAccountInfo", [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        _DEC[mint] = int(((((r or {}).get("value") or {}).get("data") or {}).get("parsed") or {}).get("info", {}).get("decimals", 6))
    return _DEC[mint]
def quote_buy(s, mint, usd):
    """Executable price for OUR ticket right now (Jupiter route quote, free). Returns (usd per token incl. impact, impact%) or (None, None)."""
    try:
        q = core.jup_quote(WSOL, mint, int(usd / C_SOL[0] * 1e9), 1000)
        if not q: return None, None
        out = int(q["outAmount"]) / 10 ** decimals(s, mint)
        return (usd / out if out > 0 else None), float(q.get("priceImpactPct") or 0) * 100
    except Exception as e: log("quote buy", str(e)[:80]); return None, None
def quote_sell(s, mint, qty):
    try:
        raw = int(qty * 10 ** decimals(s, mint))
        q = core.jup_quote(mint, WSOL, raw, 1000) if raw > 0 else None
        if not q: return None
        return int(q["outAmount"]) / 1e9 * C_SOL[0] / qty
    except Exception as e: log("quote sell", str(e)[:80]); return None
_HP = {}
def honeypot(s, mint):
    """Mirror-mode minimum check: returns a reason string if the coin is a honeypot-style trap, else None."""
    if mint in _HP: return _HP[mint]
    why = None
    try:
        r = rpc(s, "getAccountInfo", [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        info = ((((r or {}).get("value") or {}).get("data") or {}).get("parsed") or {}).get("info", {})
        if info.get("freezeAuthority"): why = "honeypot risk: freeze authority is set (issuer can freeze our tokens)"
        for e in info.get("extensions") or []:
            x = e.get("extension")
            if x in ("permanentDelegate", "nonTransferable", "transferHook") and (e.get("state") or {}) not in ({}, None) and \
               ((e.get("state") or {}).get("delegate") or (e.get("state") or {}).get("programId") or x == "nonTransferable"):
                why = f"honeypot risk: token-2022 {x} extension"
    except Exception as e: log("honeypot check", mint[:6], str(e)[:80]); return None
    _HP[mint] = why; return why
def jup_price(mints):
    out = {}
    try:
        for i in range(0, len(mints), 50):
            r = http_json("https://lite-api.jup.ag/price/v3?ids=" + ",".join(mints[i:i + 50]), timeout=8, retries=1) or {}
            for m, v in r.items():
                if v and v.get("usdPrice"): out[m] = float(v["usdPrice"])
    except Exception as e: log("jup price", str(e)[:80])
    return out
def curve_px(sw, addr, our_sol):
    """No Jupiter route: price our buy off the bonding-curve reserves in his own tx (constant product, virtual reserves for pump.fun) + 1.25% curve fee."""
    tx = sw.get("tx"); mint = sw["mint"]
    if not tx or sw["token_amount"] <= 0: return None
    m = tx["meta"]; pre = {(b["accountIndex"]): float(b["uiTokenAmount"].get("uiAmountString") or 0) for b in m.get("preTokenBalances") or [] if b["mint"] == mint and b.get("owner") != addr}
    best = None
    for b in m.get("postTokenBalances") or []:
        if b["mint"] != mint or b.get("owner") == addr: continue
        post = float(b["uiTokenAmount"].get("uiAmountString") or 0); d = pre.get(b["accountIndex"], post) - post
        if d > 0 and (best is None or d > best[0]): best = (d, pre.get(b["accountIndex"], post), post)
    if not best: return None
    off = 279_900_000 if "pump" in (sw.get("venue") or "") else 0
    avg = sw["sol_in"] / sw["token_amount"]; Y0, Y1 = best[1] + off, best[2] + off
    X = avg * Y0                      # virtual SOL reserve after his buy
    if X <= 0 or Y1 <= 0: return None
    ours = (X + our_sol) / Y1 * 1.0125
    return ours * C_SOL[0]
def lagstat(s, key, v):
    d = s["detect"]; d[key] = (d.get(key, []) + [round(v, 2)])[-50:]
def wname(s, a): return next((w for w in s["wallets"] if w["address"] == a), None)
def equity(s): return s["cash"] + sum(p["qty"] * p["last"] * (1 - SLIP_FEE_SIDE) for p in s["positions"])
def skip(s, w, sf, mint, reason, kind, ts=None, sw=None):
    s["skipped"].insert(0, dict(ts=int(time.time()), wallet=w["name"], addr=w["address"], tier=w["tier"], sym=((sf or {}).get("sym") if (sf or {}).get("sym") not in (None, "?") else None) or (sw or {}).get("sym") or mint[:4], mint=mint,
                                reason=reason, kind=kind, safety=sf, icon=(sf or {}).get("icon") or (sw or {}).get("icon", ""), url=(sf or {}).get("url", "") or f"https://solscan.io/tx/{(sw or {}).get('sig', '')}",
                                side=(sw or {}).get("side"), sig=(sw or {}).get("sig"), leaderTs=(sw or {}).get("ts"), sol=round((sw or {}).get("sol_in") or 0, 4),
                                venue=(sw or {}).get("venue"), via=(sw or {}).get("via"), seenLagS=(sw or {}).get("lag")))
    log("SKIP", w["name"], (sw or {}).get("side", ""), (sf or {}).get("sym") or mint[:6], "|", reason)
    del s["skipped"][120:]; s["tstats"][w["address"]]["skipped"] += 1
def sell(s, p, frac, why, wallet=None, px=None, lot=None):
    frac = max(0.0, min(1.0, frac))
    q = p["qty"] * frac; c = p["cost"] * frac; now = time.time()
    qpx = quote_sell(s, p["mint"], q) if FILL_SOURCE == "jupiter" else None
    if qpx: px = qpx; proceeds = max(0.0, q * px * (1 - LAND_SLIP) - PRIO_FEE_SOL * C_SOL[0])
    else: px = px or p["last"]; proceeds = q * px * (1 - SLIP_FEE_SIDE)
    s["cash"] += proceeds; p["qty"] -= q; p["cost"] -= c; pnl = proceeds - c
    if lot: p["lots"][lot] = max(0.0, p["lots"].get(lot, 0) - q)
    else:
        for a in p["lots"]: p["lots"][a] *= (1 - frac)
    for a, sh in p["contrib"].items():
        if a in s["tstats"]: s["tstats"][a]["pnl"] += pnl * sh
    s["closes"].insert(0, dict(ts=int(now), wallet=wallet or p["wallet"], wallets=p["reason"]["wallets"], sym=p["sym"], name=p["name"], icon=p["icon"],
                               url=p["url"], mint=p["mint"], share=frac, entry=p["entry"], exit=px, cost=c, proceeds=proceeds, pnl=pnl,
                               pct=(proceeds / c - 1) * 100 if c else 0, held_s=int(now - p["opened"]), exitReason=why, reason=p["reason"],
                               fill=("jupiter" if qpx else "dexscreener"), markPx=p["last"]))
    del s["closes"][300:]
    if frac >= 0.98 or p["qty"] * px < 0.05: s["positions"].remove(p)
def on_swap(s, w, sw, seen_ts):
    mint, now = sw["mint"], time.time(); age = now - sw["ts"]
    if sw["side"] == "sell":
        with L:
            p = next((p for p in s["positions"] if p["mint"] == mint and p["lots"].get(w["address"], 0) > 0), None)
            s["tstats"][w["address"]]["last"] = int(sw["ts"])
            if not p: return skip(s, w, None, mint, f"leader SOLD {sw['token_amount']:,.0f} tokens for {sw['sol_in']:.3f} SOL on {sw['venue']}; we hold none of it, nothing to mirror", "sell", sw=sw)
            if EXIT_MODE == "risk": return skip(s, w, None, mint, "leader sold; exit mode 'risk' ignores his sells", "sell", sw=sw)
        pre = sw["pre_token"] or 0; share = min(1.0, sw["token_amount"] / pre) if pre > 0 else 1.0
        time.sleep(max(0.0, EXEC_DELAY_S - (time.time() - seen_ts)))
        px = None   # sell() fills at the Jupiter quote for our size; falls back to last mark
        with L:
            if p not in s["positions"]: return
            q = min(p["qty"], p["lots"][w["address"]] * (1.0 if share >= 0.98 else share))
            sell(s, p, q / p["qty"] if p["qty"] else 1.0, "mirror sell", w["name"], px, lot=w["address"])
            log("MIRROR SELL", w["name"], p["sym"], f"{share*100:.0f}% of his bag")
        return
    sol_in = (sw.get("sol_amount") or 0) + (sw.get("usd_quote_amount") or 0) / max(C_SOL[0], 1)
    with L: s["tstats"][w["address"]]["last"] = int(sw["ts"])
    if sol_in < MIN_COPY_SOL:
        with L: return skip(s, w, None, mint, f"leader buy only {sol_in:.3f} SOL (${sol_in * C_SOL[0]:.2f}) < {MIN_COPY_SOL} SOL minimum: too small to be a real signal", "filter", sw=sw)
    if not MIRROR and mint.endswith("pump") and sw["token_amount"] > 0 and sol_in / sw["token_amount"] < max(PUMP_MIN_PX_SOL, PUMP_MIN_ENTRY_MCAP_USD / (1e9 * C_SOL[0])):
        with L: return skip(s, w, None, mint, f"his entry was at ~${sol_in / sw['token_amount'] * 1e9 * C_SOL[0]:,.0f} mcap (< ${PUMP_MIN_ENTRY_MCAP_USD:,}): launch snipe we can't reach at his price", "filter", sw=sw)
    with L:
        s["signals"] += 1; s["tstats"][w["address"]]["signals"] += 1; s["tstats"][w["address"]]["last"] = int(sw["ts"])
        r = s["recent"].setdefault(mint, {}); r[w["address"]] = sw["ts"]
        for m in list(s["recent"]):
            s["recent"][m] = {a: t for a, t in s["recent"][m].items() if now - t < CONSENSUS_WIN_S}
            if not s["recent"][m]: del s["recent"][m]
        cons = sorted(s["recent"].get(mint, {}), key=lambda a: s["recent"][mint][a])
        ncons = len(cons); names = [wname(s, a)["name"] for a in cons if wname(s, a)]
        pos = next((p for p in s["positions"] if p["mint"] == mint), None)
        if age > MAX_SIGNAL_AGE_S and sw.get("via") == "replay":
            return skip(s, w, None, mint, f"REPLAY check: his {sw.get('venue')} buy of {sw.get('sol_in', 0):.3f} SOL from {age/60:.0f} min ago was detected and parsed OK; too old to copy now", "replay", sw=sw)
        if age > MAX_SIGNAL_AGE_S: return skip(s, w, None, mint, f"seen {int(age)}s after his buy via {sw['via']} (max {MAX_SIGNAL_AGE_S}s): feed was down or catching up, too late to copy", "stale", sw=sw)
        if s["day"]["halted"]: return skip(s, w, None, mint, f"daily loss limit hit ({s['day']['why']})", "risk", sw=sw)
        if w["tier"] == "scout" and SCOUTS_NEED_CONSENSUS and ncons < CONSENSUS_N and not (pos and w["address"] in pos["lots"]):
            return skip(s, w, None, mint, f"scout wallet bought alone ({ncons}/{CONSENSUS_N} wallets in {CONSENSUS_WIN_S // 60} min); scouts only copy on consensus", "filter", sw=sw)
        if not pos and len(s["positions"]) >= MAX_OPEN: return skip(s, w, None, mint, f"{MAX_OPEN} positions open (max)", "cap", sw=sw)
        in_coin = (pos["cost"] if pos else 0)
        if COPY_ADDS == "none" and pos and pos["lots"].get(w["address"], 0) > 0: return skip(s, w, None, mint, "leader add (policy: first buy only)", "filter", sw=sw)
    if MIRROR:
        jt = sw.get("jt") or jup_token(mint)
        hp = honeypot(s, mint)
        sf = dict(ok=hp is None, fails=[hp] if hp else [], sym=jt.get("symbol") or mint[:4], name=jt.get("name", ""), icon=jt.get("icon", ""),
                  url=f"https://dexscreener.com/solana/{mint}", liq=jt.get("liquidity") or 0, mcap=jt.get("mcap") or 0, px=jt.get("usdPrice") or 0,
                  vol24=0, ageMin=None, athPct=None, holders=jt.get("holderCount"), launchpad=jt.get("launchpad"), mirror=True)
        if hp:
            with L: return skip(s, w, sf, mint, hp, "honeypot", sw=sw)
    else:
      try: sf = safety.check(mint)
      except Exception as e:
        with L: return skip(s, w, None, mint, f"coin check failed: {str(e)[:60]}", "safety", sw=sw)
    if not sf["ok"] and not MIRROR:
        if not sf.get("px") and "bonding" in sw["venue"]:
            jt = sw.get("jt") or {}
            stats = f"${jt.get('mcap') or 0:,.0f} mcap, ${jt.get('liquidity') or 0:,.0f} liquidity, {jt.get('holderCount') or 0} holders" if jt else "no liquidity data"
            why = f"still on the {sw['venue']} (not migrated): {stats}; our rules need ${MIN_LIQ_USD:,} liquidity"
        elif not sf.get("px"): why = f"no DexScreener pool for this coin ({sw['venue']}; Jupiter shows ${(sw.get('jt') or {}).get('liquidity') or 0:,.0f} liquidity): can't verify it"
        else: why = "; ".join(sf["fails"])[:160]
        with L: return skip(s, w, sf, mint, why, "safety", sw=sw)
    tk = (MIRROR_TICKET_SCOUT if w["tier"] == "scout" else MIRROR_TICKET_CORE) if MIRROR else LIVE_TICKET_USD
    tk = tk * (BOOST_X3 if ncons >= 3 else BOOST_X if ncons >= CONSENSUS_N else 1)
    tk = min(tk, MAX_PER_COIN - in_coin)
    with L:
        if tk < 1: return skip(s, w, sf, mint, f"already ${in_coin:.0f} in coin (cap ${MAX_PER_COIN})", "cap", sw=sw)
        if s["cash"] < tk: return skip(s, w, sf, mint, f"cash ${s['cash']:.2f} < ticket ${tk:.2f}", "cash", sw=sw)
    lead_px_usd = sol_in * C_SOL[0] / sw["token_amount"] if sw["token_amount"] > 0 else None
    if pos_lead_add(s, w, mint, pos):
        qp, _ = quote_buy(s, mint, tk)
        if qp and qp < pos["entry"]:
            with L: return skip(s, w, sf, mint, f"leader adding while our lot is red (now ${qp:.3g} < entry ${pos['entry']:.3g}); policy: add to winners only", "filter", sw=sw)
    time.sleep(max(0.0, EXEC_DELAY_S - (time.time() - seen_ts)))   # we would send EXEC_DELAY_S after detection
    t_fill = time.time()
    px, impact = quote_buy(s, mint, tk) if FILL_SOURCE == "jupiter" else (None, None)
    fill = "jupiter" if px else "dexscreener"
    if MIRROR:
        if px:   # sell-route check: can our bag actually be sold back to SOL?
            if not core.jup_quote(mint, WSOL, int(max(0.0, tk - PRIO_FEE_SOL * C_SOL[0]) * (1 - LAND_SLIP) / px * 10 ** decimals(s, mint)), 1000):
                with L: return skip(s, w, sf, mint, "honeypot risk: Jupiter can buy it but finds no sell route back to SOL", "honeypot", sw=sw)
        else:
            cp = curve_px(sw, w["address"], tk / C_SOL[0]) if "bonding" in (sw.get("venue") or "") else None
            if not cp:
                with L: return skip(s, w, sf, mint, f"no executable price: Jupiter has no route and no curve reserves to price from ({sw.get('venue')})", "noroute", sw=sw)
            px, fill, impact = cp, "curve", None
    if not px: px = (dexscreener_info([mint]).get(mint) or {}).get("price_usd") or sf["px"]; qty = tk * (1 - SLIP_FEE_SIDE) / px
    elif fill == "curve": qty = max(0.0, tk - PRIO_FEE_SOL * C_SOL[0]) / px
    else: qty = max(0.0, tk - PRIO_FEE_SOL * C_SOL[0]) * (1 - LAND_SLIP) / px
    vs_lead = (px / lead_px_usd - 1) * 100 if lead_px_usd else None
    kind = "consensus" if ncons >= CONSENSUS_N else "mirror"
    if MIRROR and not (px > 0 and qty > 0):
        with L: return skip(s, w, sf, mint, "no valid fill price", "noroute", sw=sw)
    with L:
        s["cash"] -= tk; s["tstats"][w["address"]]["copied"] += 1
        d = s["detect"]; d["lags"] = (d["lags"] + [round(time.time() - sw["ts"], 1)])[-50:]
        lagstat(s, "detectLags", seen_ts - sw["ts"]); lagstat(s, "fillLags", t_fill - sw["ts"])
        if vs_lead is not None and abs(vs_lead) < 500: lagstat(s, "vsLeaderPct", vs_lead)
        pos = next((p for p in s["positions"] if p["mint"] == mint), None)
        reason = dict(type=kind, wallets=names, consensus=ncons, ticket=round(tk, 2), lead=w["name"])
        if pos:
            pos["qty"] += qty; pos["cost"] += tk; pos["buys"] += 1; pos["entry"] = pos["cost"] / (pos["qty"] / (1 - SLIP_FEE_SIDE))
            pos["lots"][w["address"]] = pos["lots"].get(w["address"], 0) + qty
            pos["contrib"] = {a: q / sum(pos["lots"].values()) for a, q in pos["lots"].items()} if sum(pos["lots"].values()) else pos["contrib"]
            pos["reason"] = dict(reason, wallets=sorted(set(pos["reason"]["wallets"]) | set(names)), consensus=max(ncons, pos["reason"]["consensus"]),
                                 ticket=round(pos["reason"]["ticket"] + tk, 2), lead=pos["reason"]["lead"])
            pos["adds"].append(dict(ts=int(time.time()), wallet=w["name"], usd=round(tk, 2), px=px, consensus=ncons, fill=fill, leaderPx=lead_px_usd, vsLeaderPct=vs_lead))
        else:
            s["positions"].append(dict(mint=mint, wallet=w["name"], sym=sf["sym"], name=sf["name"], icon=sf["icon"], url=sf["url"],
                                       opened=int(time.time()), entry=px, qty=qty, cost=tk, buys=1, last=px, peak=px, tp_done=False, tpDone=False,
                                       hist=[[int(time.time()), px]], leader_age_s=int(age), liq=sf["liq"], lots={w["address"]: qty},
                                       contrib={w["address"]: 1.0}, reason=reason, safety=sf, adds=[], fill=fill, leaderPx=lead_px_usd,
                                       vsLeaderPct=(round(vs_lead, 2) if vs_lead is not None else None), impactPct=impact, fillLagS=round(t_fill - sw["ts"], 2),
                                       mode=("MIRROR" if MIRROR else "FILTERED"), venue=sw.get("venue"), leaderSig=sw.get("sig"), leaderSol=round(sw["sol_in"], 4)))
        log("BUY", sf["sym"], f"${tk:.2f}", kind, names, fill, f"vs leader {vs_lead:+.1f}%" if vs_lead is not None else "", f"lag {t_fill - sw['ts']:.1f}s")
def pos_lead_add(s, w, mint, pos):
    """True when this buy is the same leader adding to a coin we already copied from him and policy limits adds."""
    return bool(pos and pos["lots"].get(w["address"], 0) > 0 and COPY_ADDS == "winners")
def normalize_mirror_pos(p):
    """Default peak / tpDone on a restored snapshot. Does not touch cash, qty, cost, or history."""
    if "tpDone" in p: done = bool(p["tpDone"])
    else: done = bool(p.get("tp_done", False))
    p["tpDone"] = done; p["tp_done"] = done
    if p.get("peak") is None: p["peak"] = p.get("last") or p.get("entry") or 0.0
def lock_settings(s):
    c = s.get("cfg") or {}
    return (float(c.get("tp_mult", TP_MULT)), float(c.get("tp_frac", TP_FRAC)), float(c.get("trail_pct", TRAIL_PCT)))
def mirror_profit_lock(s, p):
    """Paper take-profit, then a trailing stop on whatever qty is left. Same sell() fill as a mirrored sell."""
    tp_mult, tp_frac, trail_pct = lock_settings(s)
    px, entry = p.get("last") or 0.0, p.get("entry") or 0.0
    if px <= 0 or entry <= 0 or p.get("qty", 0) <= 0: return
    if not p.get("tpDone", p.get("tp_done", False)) and px >= entry * tp_mult:
        frac = max(0.0, min(1.0, tp_frac))
        if frac <= 0: return
        part = "half" if abs(frac - 0.5) < 1e-9 else f"{frac * 100:.0f}%"
        sell(s, p, frac, f"profit lock: sold {part} at {tp_mult:g}x")   # Jupiter quote + fee path inside sell()
        if p not in s["positions"]: return
        p["tpDone"] = True; p["tp_done"] = True
    if p.get("tpDone"):
        p["peak"] = max(float(p.get("peak") or 0), px)
        p["trailPx"] = p["peak"] * (1 - trail_pct / 100.0)
        if px <= p["trailPx"]: sell(s, p, 1.0, f"trailing stop (-{trail_pct:g}% from peak)")
def risk_exits(s):
    now = time.time()
    if EXIT_MODE == "mirror":   # leader sells drive exits; profit lock plus the long safety nets
        for p in list(s["positions"]):
            normalize_mirror_pos(p)
            if now - p["opened"] > MIRROR_MAX_HOLD_H * 3600: sell(s, p, 1.0, f"safety net: held {MIRROR_MAX_HOLD_H}h")
            elif p.get("noquote_since") and now - p["noquote_since"] > MIRROR_NOQUOTE_MIN * 60:
                qp = quote_sell(s, p["mint"], p["qty"])
                if qp: sell(s, p, 1.0, f"emergency: no price feed for {MIRROR_NOQUOTE_MIN} min", px=qp)
                else: p["last"] = 0.0; sell(s, p, 1.0, f"emergency: no price and no sell route for {MIRROR_NOQUOTE_MIN} min, written off", px=1e-18)
            elif not p.get("noquote_since"):
                mirror_profit_lock(s, p)
        return
    for p in list(s["positions"]):
        px, e = p["last"], p["entry"]; p["peak"] = max(p.get("peak", px), px)
        if px <= e * (1 - STOP_LOSS): sell(s, p, 1.0, "stop loss")
        elif not p["tp_done"] and px >= e * TP1_X: p["tp_done"] = True; sell(s, p, TP1_FRAC, "take profit")
        elif p["tp_done"] and px <= p["peak"] * (1 - TRAIL): sell(s, p, 1.0, "trailing stop")
        elif now - p["opened"] > MAX_HOLD_MIN * 60: sell(s, p, 1.0, "max hold")
def mark(s):
    with L: mints = list({p["mint"] for p in s["positions"]})
    info = dexscreener_info(mints) if mints else {}
    jp = jup_price(mints) if mints and MIRROR else {}
    with L:
        for p in s["positions"]:
            px = jp.get(p["mint"]) or (info.get(p["mint"]) or {}).get("price_usd")
            if px: p["last"] = px; p["hist"].append([int(time.time()), px]); del p["hist"][:-200]; p["noquote_since"] = None
            elif not p.get("noquote_since"): p["noquote_since"] = int(time.time())
        risk_exits(s)
        eq = equity(s); now = int(time.time())
        key = datetime.datetime.now().strftime("%Y-%m-%d")
        if s["day"]["key"] != key: s["day"] = dict(key=key, start_eq=eq, halted=False, why="")
        dp = eq - s["day"]["start_eq"]; s["day"]["pnl"] = dp
        if not s["day"]["halted"] and dp <= -DAY_LOSS_PCT * s["day"]["start_eq"]:
            s["day"].update(halted=True, why=f"day P&L ${dp:.2f} <= -{DAY_LOSS_PCT*100:.0f}%"); log("HALT", s["day"]["why"])
        s["equity"].append([now, round(eq, 4)])
        if len(s["equity"]) > 4000: s["equity"] = s["equity"][::2]
def classify(tx, addr):
    """Why a leader tx is not a swap (for the feed)."""
    m = tx["meta"]; ks = [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
    signer = any((k.get("signer") if isinstance(k, dict) else False) and k.get("pubkey") == addr for k in tx["transaction"]["message"]["accountKeys"]) or (ks and ks[0] == addr)  # co-signer counts (gasless relayers pay the fee)
    toks = {}
    for b in m.get("preTokenBalances") or []:
        if b.get("owner") == addr: toks[b["mint"]] = toks.get(b["mint"], 0) - float(b["uiTokenAmount"].get("uiAmountString") or 0)
    for b in m.get("postTokenBalances") or []:
        if b.get("owner") == addr: toks[b["mint"]] = toks.get(b["mint"], 0) + float(b["uiTokenAmount"].get("uiAmountString") or 0)
    toks = {k: v for k, v in toks.items() if abs(v) > 1e-12 and k not in (core.WSOL, core.USDC, core.USDT)}
    i = ks.index(addr) if addr in ks else None
    sol = (m["postBalances"][i] - m["preBalances"][i]) / 1e9 if i is not None else 0
    if len(toks) > 1: return "multi-token tx (token->token route or batch), not copied"
    if toks and not signer: return "tokens sent to him by someone else (airdrop/spam/transfer)"
    if toks: return "token transfer (no SOL/USDC paid or received)"
    if not signer and sol > 0: return f"incoming {sol:.4f} SOL (creator fee / cashback / transfer), not a trade"
    if not signer: return "someone else's tx that only mentions his wallet"
    return "his own non-swap tx (transfer, account setup, SOL<->USDC)"
def handle_sig(s, addr, sig, seen_ts, via="ws"):
    with L:
        if sig in SEEN: return
        SEEN.add(sig)
    w = wname(s, addr)
    if not w: return
    tx = None
    for i in range(5):
        try: tx = rpc(s, "getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])
        except Exception as e: log("FEED getTransaction error", w["name"], sig[:8], str(e)[:100])
        if tx: break
        time.sleep(0.6 * (i + 1))
    if not tx:
        log("FEED ALERT tx not retrievable", w["name"], sig); feed(s, wallet=w["name"], sig=sig, via=via, kind="unreadable", note="getTransaction returned nothing after 5 tries"); return
    if tx["meta"].get("err"): return
    sws = parse_swaps(tx, addr)
    lag = round(seen_ts - (tx.get("blockTime") or seen_ts), 1)
    if not sws:
        feed(s, wallet=w["name"], sig=sig, via=via, kind="not a swap", note=classify(tx, addr), lagS=lag, leaderTs=tx.get("blockTime")); return
    v = venue(tx)
    for sw in sws:
        jt = jup_token(sw["mint"])
        sw.update(tx=tx, sig=sig, sym=jt.get("symbol"), icon=jt.get("icon", ""), jt=jt, venue=v, via=via, lag=lag, sol_in=(sw.get("sol_amount") or 0) + (sw.get("usd_quote_amount") or 0) / max(C_SOL[0], 1))
        log("SIGNAL", w["name"], sw["side"].upper(), sw.get("sym") or sw["mint"][:8], f"{sw['sol_in']:.3f} SOL", v, f"via {via} {lag:.1f}s after his tx")
        feed(s, wallet=w["name"], sig=sig, via=via, kind=sw["side"], mint=sw["mint"], sym=sw.get("sym"), sol=round(sw["sol_in"], 4), venue=v, lagS=lag, leaderTs=sw["ts"])
        with L:
            if via != "replay": lagstat(s, "seenLags", lag)
            s["detect"]["last_swap"] = int(time.time())
        try: on_swap(s, w, sw, seen_ts)
        except Exception as e: log("on_swap", w["name"], e, traceback.format_exc()[-300:])
def worker(s):
    while True:
        addr, sig, ts, via = Q.get()
        try: handle_sig(s, addr, sig, ts, via)
        except Exception as e: log("worker", e)
async def ws_main(s):
    import websockets
    while True:
        try:
            async with websockets.connect(WSS, ping_interval=None, max_size=2**22, open_timeout=15) as ws:
                async def hb():
                    while True:
                        await asyncio.sleep(WS_PING_SEC)
                        if WS_KICK[0]: WS_KICK[0] = False; log("FEED ws reconnect forced (box froze)"); await ws.close(); return
                        try: await asyncio.wait_for(await ws.ping(), WS_PING_SEC); s["detect"]["ws_pong"] = int(time.time())
                        except Exception: log("FEED ALERT ws ping timeout, reconnecting"); await ws.close(); return
                asyncio.ensure_future(hb())
                for i, w in enumerate(s["wallets"]):
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": i, "method": "logsSubscribe", "params": [{"mentions": [w["address"]]}, {"commitment": "confirmed"}]}))
                subs = {}
                s["detect"]["ws"] = True; s["detect"]["ws_since"] = int(time.time()); s["detect"]["ws_pong"] = int(time.time()); log("ws connected", len(s["wallets"]))
                POLL_NOW.set()   # catch anything that happened while we were disconnected
                async for raw in ws:
                    m = json.loads(raw)
                    if "id" in m and "result" in m: subs[m["result"]] = s["wallets"][m["id"]]["address"]; s["detect"]["subs"] = len(subs); continue
                    if "error" in m: log("FEED ALERT ws error msg", str(m)[:150]); continue
                    v = ((m.get("params") or {}).get("result") or {}).get("value") or {}
                    sub = (m.get("params") or {}).get("subscription")
                    if v.get("err") is None and v.get("signature") and sub in subs:
                        s["detect"]["last_event"] = int(time.time()); Q.put((subs[sub], v["signature"], time.time(), "websocket"))
        except Exception as e:
            s["detect"]["ws"] = False; log("FEED ALERT ws down:", str(e)[:120]); await asyncio.sleep(3)
        else: s["detect"]["ws"] = False; await asyncio.sleep(1)
def backup_poll(s):
    while True:
        ok = 0; d = s["detect"]
        for w in s["wallets"]:
            try:
                r = http_json(URL, {"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
                                    "params": [w["address"], {"limit": 25, "commitment": "confirmed", **({"until": w["last_sig"]} if w["last_sig"] else {})}]}, timeout=10, retries=1)
                s["credits"] += 1
                if "error" in r: raise RuntimeError(str(r["error"])[:120])
                sigs = r["result"]; ok += 1
                if sigs:
                    first = w["last_sig"] is None; w["last_sig"] = sigs[0]["signature"]
                    if first:
                        with L: SEEN.update(x["signature"] for x in sigs)
                    else:
                        for x in reversed(sigs):
                            if not x.get("err") and x["signature"] not in SEEN:
                                late = time.time() - (x.get("blockTime") or time.time())
                                if d.get("ws") and late > 20:
                                    d["wsMissed"] = d.get("wsMissed", 0) + 1; log("FEED ALERT websocket missed", w["name"], x["signature"][:10], f"{late:.0f}s old, caught by poll")
                                Q.put((w["address"], x["signature"], time.time(), "poll"))
            except Exception as e:
                d["poll_errors"] = d.get("poll_errors", 0) + 1; d["poll_last_error"] = f"{w['name']}: {str(e)[:80]}"; d["poll_last_error_ts"] = int(time.time())
                log("FEED ALERT poll failed", w["name"], str(e)[:100])
                if "429" in str(e): time.sleep(3)
            time.sleep(0.15)
        s["polls"] += 1; s["last_poll"] = int(time.time())
        if ok: d["poll_ok"] = int(time.time())
        POLL_NOW.wait(BACKUP_POLL_SEC); POLL_NOW.clear()
def watchdog(s):
    """Freeze detection + feed health. Never silent: status changes are logged and written to the Activity list."""
    w0, m0 = time.time(), time.monotonic(); last = None
    while True:
        time.sleep(5)
        RQ = "/workspace/desk/replay_queue.txt"   # on-demand: lines "<leader addr> <sig>" are re-run through the live parser (labelled via=replay)
        if os.path.exists(RQ):
            try:
                lines = open(RQ).read().split("\n"); os.remove(RQ)
                for ln in lines:
                    p = ln.split()
                    if len(p) == 2: Q.put((p[0], p[1], time.time(), "replay")); log("REPLAY queued", p[1][:10])
            except Exception as e: log("replay", e)
        w1, m1 = time.time(), time.monotonic(); gap = (w1 - w0) - (m1 - m0); w0, m0 = w1, m1
        d = s["detect"]; now = int(w1)
        if gap > FREEZE_GAP_S:
            d["freezes"] = d.get("freezes", 0) + 1; d["last_freeze"] = dict(ts=now, gapS=int(gap))
            log(f"FEED ALERT box was frozen ~{gap:.0f}s: forcing websocket reconnect + immediate catch-up poll"); WS_KICK[0] = True; POLL_NOW.set()
        probs = []
        if not d.get("ws"): probs.append("websocket disconnected")
        elif now - d.get("ws_pong", now) > 3 * WS_PING_SEC: probs.append(f"websocket silent {now - d.get('ws_pong', now)}s")
        elif d.get("subs", 0) < len(s["wallets"]): probs.append(f"only {d.get('subs', 0)}/{len(s['wallets'])} wallet subscriptions active")
        if now - d.get("poll_ok", now) > FEED_ALERT_POLL_S: probs.append(f"catch-up poll failing {now - d['poll_ok']}s ({d.get('poll_last_error', '')})")
        status = "live" if not probs else "down" if not d.get("ws") and len(probs) > 1 else "degraded"
        d["status"] = status; d["problems"] = probs; d["checked"] = now
        if status != last:
            msg = "feed LIVE: websocket + poll healthy" if status == "live" else f"feed {status.upper()}: " + "; ".join(probs)
            log("FEED STATUS", msg); feed(s, wallet="desk", kind="feed", note=msg)
            if last is not None:
                with L:
                    s["skipped"].insert(0, dict(ts=now, wallet="feed watchdog", addr="", tier="", sym="FEED", mint="", reason=msg, kind="feed", safety=None, icon="", url=""))
            last = status
def main():
    s = load(); save(s); log("copier2 start PAPER", len(s["wallets"]), "wallets", EXIT_MODE, "MODE=" + ("MIRROR" if MIRROR else "FILTERED"))
    s["skipped"] = [k for k in s["skipped"] if not k.get("reason", "").startswith("no DexScreener pool; coin 0s old")]
    s["wallets"] = [w for w in s["wallets"] if w["address"] not in DROP_WALLETS]
    s["mode"] = "MIRROR" if MIRROR else "FILTERED"; s.setdefault("mode_since", {})[s["mode"]] = s.get("mode_since", {}).get(s["mode"]) or int(time.time())  # old misleading rows; re-run via replay
    for _ in range(4): threading.Thread(target=worker, args=(s,), daemon=True).start()
    threading.Thread(target=backup_poll, args=(s,), daemon=True).start()
    threading.Thread(target=watchdog, args=(s,), daemon=True).start()
    if DETECT == "ws": threading.Thread(target=lambda: asyncio.run(ws_main(s)), daemon=True).start()
    while True:
        if os.path.exists(os.path.join(HERE, "PAUSE")): time.sleep(5); continue
        try: mark(s)
        except Exception as e: log("mark", e)
        save(s); time.sleep(MARK_SEC)
if __name__ == "__main__": main()
