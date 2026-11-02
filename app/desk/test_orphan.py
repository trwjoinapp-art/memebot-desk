#!/usr/bin/env python3
"""v5 offline tests: leftover-bag (orphan) guard, one-time orphan sweep, quick-flip sizing.
Paper only: every network call is stubbed. No wallet, no signing, nothing sent."""
import os, sys, time, threading
DESK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(DESK, "..", "memecoin-copytrader"))
import core
core.sol_price = lambda: 150.0
core.jup_quote = lambda *a, **k: {"outAmount": "1", "priceImpactPct": "0"}   # sell route exists
import copier2 as c
c.C_SOL[0] = 150.0
c.log = lambda *a: None
c.honeypot = lambda s, m: None
c.jup_token = lambda m: {"symbol": "TST"}
c.decimals = lambda s, m: 6
c.quote_buy = lambda s, m, usd: (0.001, 0.5)
c.quote_sell = lambda s, m, q: 0.001
c.save = lambda s: None
LEAD = "Lead1111111111111111111111111111111111111111"
MINT = "Mint11111111111111111111111111111111111pump"

def book():
    w = dict(name="Leader", address=LEAD, tier="scout", last_sig=None)
    return dict(cash=100.0, positions=[], closes=[], skipped=[], equity=[[1, 100.0]], signals=0, recent={}, wallets=[w],
                day=dict(halted=False), detect=dict(lags=[]), tstats={LEAD: dict(signals=0, copied=0, skipped=0, pnl=0.0, last=0)}), w
def swap(side, ts, tok=1000.0, pre=1000.0, mint=MINT):
    return dict(side=side, mint=mint, ts=ts, sol_in=2.5, sol_amount=2.5, usd_quote_amount=0, token_amount=tok, pre_token=pre,
                venue="pump.fun bonding curve", via="websocket", sig="sig" + side + str(ts), jt={"symbol": "TST"})
def check(cond, msg):
    if not cond: raise SystemExit("FAIL: " + msg)
    print("  ok:", msg)

def t_cancel_before_send():
    print("1) leader sells while our buy is waiting to be sent -> buy cancelled, no bag")
    c.EXEC_DELAY_S = 0.4; s, w = book(); now = time.time()
    th = threading.Thread(target=c.on_swap, args=(s, w, swap("buy", now), now)); th.start()
    time.sleep(0.1); c.on_swap(s, w, swap("sell", now + 1), time.time()); th.join()
    check(not s["positions"] and s["cash"] == 100.0, "no position, cash untouched")
    check([k["kind"] for k in s["skipped"]] == ["orphan", "orphan"], "sell logged as 'in flight', buy logged as cancelled")
    check(not c.PENDING, "pending registry cleaned up")

def t_sell_during_fill():
    print("2) leader sells while our buy is filling -> exit right after the fill")
    c.EXEC_DELAY_S = 0.0; s, w = book(); now = time.time(); orig = c.quote_buy
    def slow_quote(s_, m, usd):
        c.on_swap(s, w, swap("sell", now + 2), time.time())   # his sell is processed by another worker mid-fill
        return orig(s_, m, usd)
    c.quote_buy = slow_quote
    try: c.on_swap(s, w, swap("buy", now), now)
    finally: c.quote_buy = orig
    check(not s["positions"], "no leftover position")
    check(len(s["closes"]) == 1 and s["closes"][0]["exitReason"] == "mirror sell" and "before our fill" in s["closes"][0]["note"], "closed as mirror sell after the fill")
    check(s["tstats"][LEAD]["copied"] == 1, "the copy still counts as copied")

def t_normal_mirror():
    print("3) normal case (leader sells after our fill) still mirrors as before")
    c.EXEC_DELAY_S = 0.0; s, w = book(); now = time.time()
    c.on_swap(s, w, swap("buy", now), now)
    check(len(s["positions"]) == 1 and abs(s["positions"][0]["cost"] - c.MIRROR_TICKET_SCOUT) < 1e-9, "full scout ticket with no hold history")
    c.on_swap(s, w, swap("sell", now + 120), time.time())
    check(not s["positions"] and s["closes"][0]["exitReason"] == "mirror sell" and "note" not in s["closes"][0], "mirrored exit")

def t_sweep():
    print("4) one-time sweep exits lots whose leader balance is 0, keeps the rest, never sells on an RPC error")
    s, w = book(); other = "Othr1111111111111111111111111111111111111111"
    s["wallets"].append(dict(name="Other", address=other, tier="core")); s["tstats"][other] = dict(signals=0, copied=0, skipped=0, pnl=0.0, last=0)
    mk = lambda m, lots: dict(mint=m, wallet="Leader", sym=m[:4], name="", icon="", url="", opened=time.time() - 600, entry=0.001, qty=sum(lots.values()),
                              cost=5.0 * len(lots), buys=1, last=0.0006, hist=[], lots=lots, contrib={a: 1 / len(lots) for a in lots},
                              reason=dict(type="mirror", wallets=["Leader"], consensus=1, ticket=5, lead="Leader"), adds=[])
    s["positions"] = [mk("PEPEKIN", {LEAD: 5000.0}), mk("KABOSU", {LEAD: 5000.0, other: 5000.0}), mk("HELD", {LEAD: 5000.0}), mk("ERR", {LEAD: 5000.0})]
    bal = {("PEPEKIN", LEAD): 0, ("KABOSU", LEAD): 0, ("KABOSU", other): 120.0, ("HELD", LEAD): 9e6}
    def fake(addr, mint):
        if mint == "ERR": raise RuntimeError("429")
        return bal[(mint, addr)]
    out = c.orphan_sweep(s, fake); print("    exited:", out)
    check(sorted(out) == [("KABO", "Leader"), ("PEPE", "Leader")], "exited PEPEKIN + Leader's KABOSU lot")
    check(sorted(p["sym"] for p in s["positions"]) == ["ERR", "HELD", "KABO"], "HELD, ERR and Other's KABOSU lot stay open")
    kb = next(p for p in s["positions"] if p["sym"] == "KABO")
    check(kb["lots"][LEAD] == 0 and kb["lots"][other] == 5000.0, "only the sold leader's lot was exited")
    check(s["_sweepErrs"] == 1, "RPC error counted, so the one-time flag is not set and it retries next start")

def t_quick_flip():
    print("5) quick-flip rule replayed on Numer0's real 1 Nov feed (leader timestamps from /api/state 11:16 PM EST)")
    import json
    ev = json.load(open(os.path.join(DESK, "test_data", "numer0_timeline.json")))
    s, w = book(); sized = []
    for ts, side, mint, sym, sol in ev:
        if side == "buy" and sol >= c.MIN_COPY_SOL: sized.append((sym, c.flip_info(s, LEAD)[0]))
        c.lead_hold(s, LEAD, mint, dict(side=side, ts=ts))
    holds = [h[2] for h in s["leadHold"][LEAD]["holds"]]
    print("    round trips (s):", holds); print("    ticket multiplier per copyable buy:", sized)
    check(holds == [2, 12, 71, 2, 10, 12, 146, 983, 3], "round-trip holds match the feed")
    check([m for _, m in sized] == [1, 1, 1, 1, 1, 1, 0.5, 0.5, 0.5, 0.5, 0.5], "PEPEHOUSE, RFF, 7vNP, Kabosu and the 11:03 PM PEPE2 add at 0.5x; the 10:28/10:30 PM PEPE2 winners at full size")
    _, st = c.flip_info(s, LEAD); check(st["n"] == 6 and st["fast"] == 4 and st["on"], "last-6 stats exposed for the traders panel")

if __name__ == "__main__":
    for t in (t_cancel_before_send, t_sell_during_fill, t_normal_mirror, t_sweep, t_quick_flip): t()
    print("ALL OK")
