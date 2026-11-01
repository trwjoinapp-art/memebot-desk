import json, os, time, datetime, glob, statistics
from export2 import logo, spark, tz
D = os.environ.get("DESK_DIR", "/workspace/desk")
def t(ts): return datetime.datetime.fromtimestamp(ts, tz).strftime('%-I:%M %p %Z')
def trail_px(p, cfg):
    """Open-position trail. Mirror mode uses the profit-lock trail; other modes keep the old cfg.trail formula."""
    done = bool(p.get("tpDone", p.get("tp_done", False)))
    if cfg.get("exitMode") == "mirror" or cfg.get("mirror"):
        if p.get("trailPx") is not None: return p["trailPx"]
        if not done: return None
        peak = p.get("peak")
        if peak is None: peak = p.get("last") or p.get("entry") or 0
        return peak * (1 - float(cfg.get("trail_pct", 30)) / 100.0)
    if done: return p.get("peak", 0) * (1 - cfg.get("trail", 0))
    return None
def J(p):
    try: return json.load(open(p))
    except Exception: return None
_mom = None
def momentum():
    global _mom
    if _mom is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "momentum_session.json")
        if not os.path.exists(p): return None
        s = json.load(open(p)); cl = s["closes"]; eq = s["equity"][-1][1] if s["equity"] else 100
        _mom = dict(final=eq, trades=len(cl), wins=sum(1 for c in cl if c["pnl"] > 0), realized=sum(c["pnl"] for c in cl),
                    open=len(s["positions"]), started=t(s["started"]), ended=t(s["equity"][-1][0]) if s["equity"] else "")
    return _mom
_prev = {}
def prev_sessions():
    out = []
    for f in sorted(glob.glob(f"{D}/archive/desk_session_*.json")):
        if f not in _prev:
            s = J(f) or {}; eq = s.get("equity") or [[s.get("started", 0), 100]]
            _prev[f] = dict(file=os.path.basename(f), started=t(s.get("started", eq[0][0])), ended=t(eq[-1][0]), final=round(eq[-1][1], 2),
                            closes=len(s.get("closes", [])), open=len(s.get("positions", [])))
        out.append(_prev[f])
    return out
def trow(r, tier):
    return dict(name=r["name"], addr=r["address"], buys=r["buys"], coins=r["coins"], put=round(r["put_usd"], 2), got=round(r["got_usd"], 2),
                x=round(r.get("copy_x", 0), 2), bpd=r["buys_per_day"], hold=r["median_hold_s"], days=r["days"], flags=r["flags"],
                best=r.get("best_coin_sym"), bestUsd=round(r.get("best_coin_usd") or 0, 2),
                tier=tier, score=r.get("score"), xReal=round(r.get("x_real", 0), 3), xExBest=round(r.get("x_ex_best", 0), 3),
                winRate=round(r.get("win", 0), 3), pf=round(min(r.get("pf", 0), 99), 2), greenDays=round(r.get("green", 0), 3),
                activeDays=r.get("active_days"), txs=r.get("txs"), src=r.get("src"), xRisk=r.get("x_risk"),
                why=r.get("why"), x7=r.get("x7"), idleH=r.get("idle_h"), zeroShare=r.get("zero_share"), entryMcapMed=r.get("entry_mcap_med"),
                snipeShare=r.get("snipe_share"), avgBuyUsd=r.get("avg_buy_usd"), stTags=r.get("st_tags"), address=r.get("address"),
                handle=r.get("x"), sources=r.get("sources"), platforms=r.get("platforms"), lead30=r.get("lead30"), lead7=r.get("lead7"), historySrc=r.get("history_src"))
def build():
    v3 = os.path.exists(f"{D}/shortlist3.json"); v2 = v3 or os.path.exists(f"{D}/shortlist2.json")
    sl = J(f"{D}/shortlist3.json") if v3 else J(f"{D}/shortlist2.json") if v2 else J(f"{D}/shortlist.json")
    st = J(f"{D}/desk_state.json") if os.path.exists(f"{D}/desk_state.json") else None
    bt = J(f"{D}/backtest_exits.json")
    now = time.time()
    out = dict(mode="desk", version=2, asOf=t(now), momentum=momentum(), prevSessions=prev_sessions(),
               backtest=dict(generated=t(sl["generated"]), candidates=sl["candidates"], tested=len(sl["all"]), passed=len(sl["shortlist"]),
                             credits=sl["helius_credits"], sol=sl["sol_usd"],
                             greenCopies=sum(1 for r in sl["all"] if r["got_usd"] > r["put_usd"] and r["buys"] > 0),
                             withBuys=sum(1 for r in sl["all"] if r["buys"] > 0),
                             sources=sl.get("sources", []), txCap=(sl.get("stages") or {}).get("p2", 15) * 100, scouts=len(sl.get("scouts", [])),
                             stages=sl.get("stages"), delayPen=sl.get("delay_pen"), slip=sl.get("slip"),
                             auditVersion=sl.get("version"), gates=sl.get("gates"), hygiene=sl.get("hygiene"), srcCounts=sl.get("src_counts"),
                             winners=[dict(sym=w.get("sym"), mint=w.get("mint"), src=w.get("src")) for w in sl.get("winners", [])]),
               shortlist=[trow(r, "core") for r in sl["shortlist"]],
               scouts=[trow(r, "scout") for r in sl.get("scouts", [])],
               near=[dict(name=r["name"], x=round(r.get("x_real", r.get("copy_x", 0)), 2), buys=r["buys"], why="; ".join(r["drop"]),
                          xExBest=round(r.get("x_ex_best", 0), 2), score=r.get("score"))
                     for r in sl["all"] if r["drop"] and r["buys"] > 0 and not r.get("tier")][:12],
               strategy=(dict(exitRule=(st or {}).get("cfg", {}).get("exitMode"), chosen=bt["chosen"], chosenWhy=bt["chosenWhy"], days=bt["days"],
                              coins=bt["coins"], delayPen=bt["delay_pen"],
                              compare=[{k: c[k] for k in ("rule", "desc", "start", "final", "pnl", "x", "trades", "winRate", "pf", "maxDD", "put", "exits")} for c in bt["compare"]])
                         if bt else None))
    out["hype"] = hype()
    if not st: out.update(live=False); return out
    cfg = dict(st["cfg"]); slip = cfg["slip"]
    cfg.setdefault("tp_mult", 2.0); cfg.setdefault("tp_frac", 0.5); cfg.setdefault("trail_pct", 30)
    names = {w["address"]: w for w in st["wallets"]}
    pos = []
    for p in st["positions"]:
        v = p["qty"] * p["last"] * (1 - slip)
        sf = p.get("safety") or {}
        pos.append(dict(addr=p["mint"], qty=p["qty"], sym=p["sym"], name=p["name"], logo=logo(p["icon"]), url=p["url"], wallet=p["wallet"],
                        held=int((now - p["opened"]) / 60), cost=round(p["cost"], 4), value=round(v, 4), pnl=round(v - p["cost"], 4),
                        pct=round((v / p["cost"] - 1) * 100, 2) if p["cost"] else 0, entry=p["entry"], last=p["last"], move=round((p["last"] / p["entry"] - 1) * 100, 2),
                        buys=p["buys"], lag=p.get("leader_age_s"), spark=spark(p["hist"]),
                        reason=p.get("reason"), safety={k: sf.get(k) for k in ("ok", "liq", "vol24", "ageMin", "mcap", "mintAuth", "freezeAuth", "top10Pct", "topPct", "athPct", "fails")} if sf else None,
                        fill=p.get("fill"), vsLeaderPct=p.get("vsLeaderPct"), impactPct=p.get("impactPct"), fillLagS=p.get("fillLagS"),
                        peak=p.get("peak"), tpDone=bool(p.get("tpDone", p.get("tp_done", False))), openedTs=p["opened"],
                        stopPx=(p["entry"] * (1 - cfg.get("stopLoss", 1)) if cfg.get("exitMode") != "mirror" else None),
                        trailPx=trail_px(p, cfg)))
    cl = [dict(sym=c["sym"], name=c["name"], logo=logo(c["icon"]), url=c["url"], wallet=c["wallet"], at=t(c["ts"]), ts=c["ts"], held=int(c["held_s"] / 60),
               entry=c["entry"], exit=c["exit"], cost=round(c["cost"], 4), pnl=round(c["pnl"], 4), pct=round(c["pct"], 2), share=round(c["share"] * 100),
               exitReason=c.get("exitReason", "mirror sell"), reason=c.get("reason"), fill=c.get("fill")) for c in st["closes"][:30]]
    sk = [dict(at=t(x["ts"]), ts=x["ts"], wallet=x["wallet"], sym=x["sym"], reason=x["reason"], kind=x.get("kind"), safety=x.get("safety"),
               logo=logo(x["icon"]) if x.get("icon") else "", url=x.get("url", ""), side=x.get("side"), sig=x.get("sig"), leaderTs=x.get("leaderTs"),
               sol=x.get("sol"), venue=x.get("venue"), via=x.get("via"), seenLagS=x.get("seenLagS")) for x in st["skipped"][:25]]
    eq = st["equity"]; step = max(1, len(eq) // 300); eq = eq[::step] + ([eq[-1]] if (len(eq) - 1) % step else [])
    day = st.get("day") or {}
    det = st.get("detect") or {}
    ts_ = st.get("tstats") or {}
    out.update(live=True, startTs=st["started"], started=t(st["started"]), lastTs=max(st.get("last_poll") or 0, det.get("last_event") or 0) or st["started"],
               polls=st["polls"], cash=round(st["cash"], 4), credits=st["credits"] + st.get("safety_credits", 0), signals=st["signals"], skippedN=len(st["skipped"]),
               mode=st.get("mode", "FILTERED"), modeSince=(st.get("mode_since") or {}).get(st.get("mode", "")), equity=eq, positions=pos, closes=cl, skipped=sk, cfg=cfg, watching=[w["name"] for w in st["wallets"]], closesN=len(st["closes"]),
               risk=dict(dayPnl=round(day.get("pnl", 0), 4), dayLossLimit=round(cfg.get("dayLoss", 0) * day.get("start_eq", 100), 2), halted=day.get("halted", False),
                         haltReason=day.get("why", ""), openN=len(st["positions"]), maxOpen=cfg.get("maxOpen"), perCoinCap=cfg.get("max_coin"), cash=round(st["cash"], 4)),
               detect=dict(mode=det.get("mode", "poll"), pollSec=cfg.get("poll"), lastEventTs=det.get("last_event"), wsConnected=det.get("ws", False),
                           medianLagS=(statistics.median(det["lags"]) if det.get("lags") else None),
                           detectLagMedS=(statistics.median(det["detectLags"]) if det.get("detectLags") else None),
                           fillLagMedS=(statistics.median(det["fillLags"]) if det.get("fillLags") else None),
                           vsLeaderMedPct=(statistics.median(det["vsLeaderPct"]) if det.get("vsLeaderPct") else None),
                           fillSource=cfg.get("fillSource"), execDelayS=cfg.get("delay"),
                           status=det.get("status"), problems=det.get("problems", []), lastSwapTs=det.get("last_swap"), wsPongTs=det.get("ws_pong"),
                           wsSince=det.get("ws_since"), subs=det.get("subs"), pollOkTs=det.get("poll_ok"), pollErrors=det.get("poll_errors", 0),
                           pollLastError=det.get("poll_last_error"), wsMissed=det.get("wsMissed", 0), freezes=det.get("freezes", 0), lastFreeze=det.get("last_freeze"),
                           seenLagMedS=(statistics.median(det["seenLags"]) if det.get("seenLags") else None)),
               feed=[dict(f) for f in (st.get("feed") or [])[:60]],
               traders=[dict(name=w["name"], addr=a, tier=w.get("tier", "core"), signals=ts_.get(a, {}).get("signals", 0), copied=ts_.get(a, {}).get("copied", 0),
                             skipped=ts_.get(a, {}).get("skipped", 0), pnl=round(ts_.get(a, {}).get("pnl", 0), 4), lastSeenTs=ts_.get(a, {}).get("last") or None,
                             lastSeen=t(ts_[a]["last"]) if ts_.get(a, {}).get("last") else None) for a, w in names.items()])
    return out

# ---------------------------------------------------------------- hype sniper (strategy 2, own $100 paper balance)
def _links(mint, tw=None, web=None, tg=None):
    return dict(pump=f"https://pump.fun/coin/{mint}" if mint.endswith("pump") else None, dex=f"https://dexscreener.com/solana/{mint}",
                solscan=f"https://solscan.io/token/{mint}", x=tw or None, web=web or None, tg=tg or None)
def hype():
    s = J(f"{D}/hype_state.json")
    if not s or s.get("strategy") != "hype": return dict(live=False)
    now = time.time(); c = s.get("cfg") or {}; slip = 0.02
    start = float(s.get("startUsd") or c.get("start_usd") or 100)
    tp, trail, stop = float(c.get("tp_mult", 2)), float(c.get("trail_pct", 30)), float(c.get("stop_pct", 35))
    pos = []
    for p in s.get("positions") or []:
        val = p["qty"] * p["last"] * (1 - slip); x = p["last"] / p["entry"] if p.get("entry") else 0
        pos.append(dict(mint=p["mint"], sym=p.get("sym"), name=p.get("name"), icon=p.get("icon"), score=p.get("score"), cats=p.get("cats"), bd=p.get("bd"),
                        opened=p["opened"], openedAt=t(p["opened"]), held=int(now - p["opened"]), entry=p["entry"], last=p["last"], peak=p.get("peak"),
                        x=round(x, 3), value=round(val, 4), cost=round(p["cost"], 4), cost0=p.get("cost0"), realized=round(p.get("realized", 0), 4),
                        pnl=round(val + p.get("realized", 0) - (p.get("cost0") or p["cost"]), 4), tpDone=bool(p.get("tpDone")),
                        tpPx=p["entry"] * tp, stopPx=p["entry"] * (1 - stop / 100), trailPx=p.get("trailPx") if p.get("tpDone") else None,
                        impactPct=p.get("impactPct"), roundTripPct=p.get("roundTripPct"), entryMcap=p.get("entryMcap"), chain=p.get("chain"),
                        xinfo=p.get("x"), spark=[h[1] for h in (p.get("hist") or [])[-60:]], noPrice=bool(p.get("noquote_since")),
                        links=_links(p["mint"], p.get("twitter"), p.get("website"), p.get("telegram")), sells=p.get("sells") or []))
    closes = s.get("closes") or []
    eq = s["cash"] + sum(p["value"] for p in pos)
    ser = s.get("equity") or []
    step = max(1, len(ser) // 160)
    wins = sum(1 for x in closes if x.get("pnl", 0) > 0)
    for x in closes: x["closedAt"] = t(x["closed"]); x["held"] = x["closed"] - x["opened"]; x["links"] = _links(x["mint"])
    for d in s.get("decisions") or []: d["at"] = t(d["ts"]); d["links"] = _links(d["mint"], d.get("twitter"))
    cands = s.get("candidates") or []
    for k in cands: k["links"] = _links(k["mint"], k.get("twitter"), k.get("website"), k.get("telegram"))
    lp = s.get("last_poll") or 0
    return dict(live=True, asOf=t(now), lastPoll=lp, stale=(now - lp) > 120, startUsd=start, cash=round(s["cash"], 4), equity=round(eq, 4),
                pnl=round(eq - start, 4), pnlPct=round((eq / start - 1) * 100, 2), realized=round(sum(x.get("pnl", 0) for x in closes), 4),
                unreal=round(sum(p["value"] - p["cost"] for p in pos), 4), trades=len(closes), wins=wins, losses=len(closes) - wins,
                open=pos, closes=closes[:40], decisions=(s.get("decisions") or [])[:60], candidates=cands[:25],
                sources=s.get("sources") or {}, metas=s.get("metas") or [], stats=s.get("stats") or {}, pool=s.get("pool"), solUsd=s.get("solUsd"),
                started=s.get("started"), startedAt=t(s["started"]) if s.get("started") else None,
                series=[[a, b] for a, b in ser[::step]] + ([ser[-1]] if ser and (len(ser) - 1) % step else []),
                cfg={k: c.get(k) for k in ("ticket_usd", "max_open", "min_score", "tp_mult", "tp_frac", "trail_pct", "stop_pct", "max_hold_h",
                                           "max_age_h", "min_traders_5m", "min_mcap", "max_mcap", "min_liq", "max_top10_pct", "max_top1_pct",
                                           "max_dev_pct", "max_roundtrip_loss_pct", "copycat_ratio", "hype_handles", "hype_keywords")})
