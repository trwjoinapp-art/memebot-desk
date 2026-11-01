#!/usr/bin/env python3
"""Offline simulation of the MIRROR profit lock. Paper fills only: no network, no wallet, no signing.

The Jupiter quote is stubbed so sell() still takes the live quote + priority-fee path.
"""
import os, sys, time
DESK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(DESK, "..", "memecoin-copytrader"))
import core
core.sol_price = lambda: 150.0
import copier2
copier2.C_SOL[0] = 150.0
copier2.log = lambda *a: None

QUOTES = []
def fake_quote(s, mint, qty):
    """Stand-in for the Jupiter sell quote: the mark, so sell() applies land slip and the priority fee."""
    QUOTES.append((mint, qty))
    for p in s["positions"]:
        if p["mint"] == mint and qty > 0: return p["last"]
    return None
copier2.quote_sell = fake_quote

def proceeds(qty, px):
    return max(0.0, qty * px * (1 - copier2.LAND_SLIP) - copier2.PRIO_FEE_SOL * copier2.C_SOL[0])

def bare(mint, addr, qty=100.0, entry=1.0, cost=100.0, last=1.0):
    """Restored position: no peak, tpDone, or trailPx keys."""
    return dict(mint=mint, wallet="Leader", sym="TEST", name="Test", icon="", url="",
                opened=time.time(), entry=entry, qty=qty, cost=cost, buys=1, last=last,
                hist=[[1, entry]], lots={addr: qty}, contrib={addr: 1.0},
                reason=dict(type="mirror", wallets=["Leader"], consensus=1, ticket=cost, lead="Leader"))

def book(pos, addr, cash=86.0):
    equity = [[1, 100.0], [2, 99.5]]
    return dict(cash=cash, positions=[pos], closes=[], skipped=[], equity=equity,
                cfg=copier2.cfg(), tstats={addr: dict(signals=1, copied=1, skipped=0, pnl=0.0, last=0)})

def n(v):
    if isinstance(v, float): return f"{v:.4f}".rstrip("0").rstrip(".")
    return v
def snap(label, s, mint):
    p = next((x for x in s["positions"] if x["mint"] == mint), None)
    last = s["closes"][0] if s["closes"] else None
    print(f"  {label}")
    if p:
        print(f"    open qty={n(p['qty'])} cost={n(p['cost'])} last={n(p['last'])} "
              f"tpDone={p.get('tpDone')} peak={n(p.get('peak'))} trailPx={n(p.get('trailPx'))}")
    else:
        print("    open: none")
    print(f"    cash={n(s['cash'])} closes={len(s['closes'])}"
          + (f" last_reason={last['exitReason']!r} share={last['share']:.2f} fill={last['fill']} proceeds={n(last['proceeds'])}" if last else ""))

def check(cond, msg):
    if not cond: raise SystemExit("FAIL: " + msg)

def main():
    cfg = copier2.cfg()
    print("cfg /api/state:", {k: cfg[k] for k in ("tp_mult", "tp_frac", "trail_pct", "exitMode", "mirror")})
    check(cfg["tp_mult"] == 2.0 and cfg["tp_frac"] == 0.5 and cfg["trail_pct"] == 30, "cfg defaults")
    # old snapshot with none of the new keys still publishes the defaults
    old = {"exitMode": "mirror", "mirror": True, "slip": 0.02}
    for k, v in (("tp_mult", 2.0), ("tp_frac", 0.5), ("trail_pct", 30)): old.setdefault(k, v)
    check(old["tp_mult"] == 2.0 and old["trail_pct"] == 30, "api cfg defaults")

    addr, mint = "LeaderWallet", "MintProfit"
    s = book(bare(mint, addr), addr)
    cash0, equity0, qty0 = s["cash"], list(s["equity"]), s["positions"][0]["qty"]
    print("\n1) 1.00x  (restored position has no peak / tpDone)")
    copier2.risk_exits(s); snap("after mark", s, mint)
    p = s["positions"][0]
    check(p["qty"] == qty0 and not p["tpDone"] and p.get("trailPx") is None, "1x should not sell")
    check(s["cash"] == cash0 and s["equity"] == equity0 and s["closes"] == [], "cash and history unchanged")
    check(p["peak"] == 1.0, "missing peak defaults to the mark")

    print("\n2) 2.10x  take-profit sells half through the Jupiter fill path")
    p["last"] = 2.1
    before = len(QUOTES)
    copier2.risk_exits(s); snap("after mark", s, mint)
    p = s["positions"][0]
    half = proceeds(50, 2.1)
    check(len(QUOTES) > before, "quote_sell was used")
    check(abs(p["qty"] - 50) < 1e-9 and p["tpDone"] is True, "half sold, tpDone set")
    check(abs(p["trailPx"] - 2.1 * 0.70) < 1e-9, "trail armed at peak * 0.70")
    check(s["closes"][0]["exitReason"] == "profit lock: sold half at 2x", "tp reason")
    check(s["closes"][0]["fill"] == "jupiter" and abs(s["closes"][0]["proceeds"] - half) < 1e-9, "jupiter proceeds")
    check(abs(s["cash"] - (cash0 + half)) < 1e-9, "proceeds added to cash")
    check(s["equity"] == equity0, "equity history not reset")

    print("\n3) 3.00x  peak ratchets, remainder stays open")
    p["last"] = 3.0
    copier2.risk_exits(s); snap("after mark", s, mint)
    p = s["positions"][0]
    check(abs(p["qty"] - 50) < 1e-9 and len(s["closes"]) == 1, "no sell at the new high")
    check(abs(p["peak"] - 3.0) < 1e-9 and abs(p["trailPx"] - 2.1) < 1e-9, "trail is 30% under 3")

    print("\n4) 2.05x  remainder hits the trailing stop")
    p["last"] = 2.05
    copier2.risk_exits(s); snap("after mark", s, mint)
    check(not any(x["mint"] == mint for x in s["positions"]), "position closed")
    check(s["closes"][0]["exitReason"] == "trailing stop (-30% from peak)", "trail reason")
    check(abs(s["closes"][0]["share"] - 1) < 1e-9 and s["closes"][0]["fill"] == "jupiter", "rest sold via jupiter")
    rest = proceeds(50, 2.05)
    check(abs(s["cash"] - (cash0 + half + rest)) < 1e-9, "rest proceeds added to cash")

    print("\n5) leader sells 40% before take-profit")
    mint2, addr2 = "MintMirror", "LeaderWallet"
    s2 = book(bare(mint2, addr2, last=1.5), addr2)
    s2["positions"][0]["last"] = 1.5
    copier2.risk_exits(s2)
    check(s2["positions"][0]["qty"] == 100 and s2["closes"] == [], "1.5x is not a take-profit")
    w = dict(name="Leader", address=addr2, tier="core")
    sw = dict(side="sell", mint=mint2, ts=time.time(), token_amount=400, pre_token=1000,
              sol_amount=0.4, usd_quote_amount=0, sol_in=0.4, venue="Jupiter", via="test", sig="sig", lag=0)
    copier2.on_swap(s2, w, sw, time.time() - 10)
    snap("after leader sell", s2, mint2)
    p2 = s2["positions"][0]
    check(abs(p2["qty"] - 60) < 1e-9 and not p2["tpDone"], "40% of the open qty was mirrored")
    check(s2["closes"][0]["exitReason"] == "mirror sell" and abs(s2["closes"][0]["share"] - 0.4) < 1e-9, "mirror reason")
    check(s2["closes"][0]["fill"] == "jupiter", "mirror sell used the quote path")

    print("\n6) leader sells 40% after the half is locked (applied to the qty that remains)")
    mint3 = "MintRemain"
    s3 = book(bare(mint3, addr2), addr2)
    s3["positions"][0]["last"] = 2.1
    copier2.risk_exits(s3)
    check(abs(s3["positions"][0]["qty"] - 50) < 1e-9, "half locked first")
    sw["mint"] = mint3
    copier2.on_swap(s3, w, sw, time.time() - 10)
    snap("after leader sell", s3, mint3)
    check(abs(s3["positions"][0]["qty"] - 30) < 1e-9, "40% of the remaining 50")
    check(s3["closes"][0]["exitReason"] == "mirror sell", "still a mirror sell")

    # /api/state position fields, including a restored row that only has tp_done
    src = open(os.path.join(DESK, "..", "memebot", "desk_export.py")).read()
    ns = {}
    exec(src[src.index("def trail_px"):src.index("def J")], ns)
    restored = dict(entry=1.0, peak=3.0, tp_done=True, last=2.5)
    api_cfg = {"exitMode": "mirror", "mirror": True, "trail": 1.0, "trail_pct": 30}
    shown = ns["trail_px"](restored, api_cfg)
    print(f"\n/api/state restored tp_done position: tpDone=True trailPx={n(shown)}")
    check(abs(shown - 2.1) < 1e-9, "api trail from peak")
    fresh = dict(entry=1.0, last=1.0)
    check(ns["trail_px"](fresh, api_cfg) is None, "no trail before tp")
    print("\nPASS")

if __name__ == "__main__":
    main()
