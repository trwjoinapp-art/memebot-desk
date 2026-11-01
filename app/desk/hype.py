# hype.py · PAPER hype sniper (strategy 2, own $100). Finds fresh Solana meme launches with real social + trading hype
# from FREE public feeds and paper-enters only when the hype score and every safety gate pass.
# No wallet, no private key, no signing, no transactions sent. Every fill is a real Jupiter quote + fees, simulated in hype_state.json.
import sys, os, json, time, threading, re, datetime, traceback, urllib.request, urllib.error, urllib.parse, queue
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "memecoin-copytrader")))   # local clone first, /workspace on Render
import core
from core import http_json
import config as C
import safety
assert C.PAPER is True
SF = os.path.join(HERE, "hype_state.json"); CF = os.path.join(HERE, "hype_cfg.json")
WSOL = "So11111111111111111111111111111111111111112"
PRIO_FEE_SOL, LAND_SLIP, SLIP = C.PRIO_FEE_SOL, C.LAND_SLIP, C.SLIP_FEE_SIDE
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json"}
L = threading.RLock()
POOL = {}          # mint -> candidate
SOL = [0.0]
SOCQ = queue.Queue(); SOC_PENDING = set(); XCACHE = {}
STOP = {"bigcap": {"SOL", "WSOL", "USDC", "USDT", "CBBTC", "JUP", "BONK", "WIF", "JTO", "PYTH", "RAY", "ETH", "WBTC", "TRUMP"}}

def cfg():
    c = dict(getattr(C, "HYPE", {}))
    try:
        if os.path.exists(CF): c.update(json.load(open(CF)))
    except Exception as e: log("hype_cfg.json ignored:", str(e)[:80])
    return c
H = cfg()
def log(*a): core.log("[hype]", *a)

# ------------------------------------------------------------------ http + per-source health
HEALTH = {}
def get(url, src, headers=None, timeout=12):
    h = HEALTH.setdefault(src, dict(ok=False, nOk=0, nErr=0, lastOk=0, lastErr=0, err="", fails=0))
    try:
        req = urllib.request.Request(url, headers={**UA, **(headers or {})})
        with urllib.request.urlopen(req, timeout=timeout) as r: d = json.loads(r.read())
        h.update(ok=True, lastOk=int(time.time()), fails=0); h["nOk"] += 1
        return d
    except urllib.error.HTTPError as e: err = f"HTTP {e.code}"
    except Exception as e: err = type(e).__name__ + ": " + str(e)[:60]
    h.update(ok=False, lastErr=int(time.time()), err=core.redact(err)); h["nErr"] += 1; h["fails"] += 1
    return None
def backoff(src, base):
    f = HEALTH.get(src, {}).get("fails", 0)
    return base if not f else min(600, base * 2 ** min(f, 6))

# ------------------------------------------------------------------ candidate pool
def cand(mint, src):
    c = POOL.get(mint)
    if not c:
        ST["stats"]["seen"] = ST["stats"].get("seen", 0) + 1
        c = POOL[mint] = dict(mint=mint, sym="", name="", icon="", desc="", twitter="", website="", telegram="", created=0,
                              first=int(time.time()), src={}, snaps=[], j={}, pump={}, st={}, x=None, score=None, cats={}, bd=[],
                              nextCheck=0, status="watching", lastDecision=0, dead=False)
    c["src"][src] = int(time.time())
    return c
def iso(s):
    try: return int(datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception: return 0

def ingest_pump(rows, src):
    for r in rows or []:
        m = r.get("mint")
        if not m or r.get("complete") is None and not r.get("created_timestamp"): continue
        with L:
            c = cand(m, src)
            for k in ("name", "symbol", "description", "twitter", "website", "telegram", "image_uri"):
                v = r.get(k)
                if v: c[{"symbol": "sym", "description": "desc", "image_uri": "icon"}.get(k, k)] = v if k != "description" else str(v)[:300]
            c["created"] = c["created"] or int((r.get("created_timestamp") or 0) / 1000)
            p = c["pump"]
            for k in ("usd_market_cap", "ath_market_cap", "king_of_the_hill_timestamp", "is_currently_live", "complete", "last_trade_timestamp", "creator"):
                if r.get(k) is not None: p[k] = r[k]
            if src == "pump.live": p["is_currently_live"] = True
def ingest_jup(rows, src):
    for r in rows or []:
        m = r.get("id")
        if not m: continue
        with L:
            if src != "jup.recent" and m not in POOL and (r.get("symbol") or "").upper() in STOP["bigcap"]: continue
            c = cand(m, src); jupset(c, r)
def jupset(c, r):
    c["j"] = r; c["sym"] = c["sym"] or r.get("symbol") or ""; c["name"] = c["name"] or r.get("name") or ""
    c["icon"] = c["icon"] or r.get("icon") or ""
    for k in ("twitter", "website", "telegram"):
        if r.get(k) and not c[k]: c[k] = r[k]
    c["created"] = c["created"] or iso(r.get("createdAt") or (r.get("firstPool") or {}).get("createdAt") or "")
    s5 = r.get("stats5m") or {}
    c["snaps"].append(dict(t=int(time.time()), mcap=r.get("mcap") or 0, holders=r.get("holderCount") or 0, traders=s5.get("numTraders") or 0))
    del c["snaps"][:-30]
def ingest_dex(rows, src):
    for r in rows or []:
        if r.get("chainId") != "solana" or not r.get("tokenAddress"): continue
        with L:
            c = cand(r["tokenAddress"], src)
            for l in r.get("links") or []:
                u = l.get("url") or ""; t = (l.get("type") or l.get("label") or "").lower()
                if ("twitter" in t or "x.com" in u or "twitter.com" in u) and not c["twitter"]: c["twitter"] = u
                elif "telegram" in t and not c["telegram"]: c["telegram"] = u
                elif "website" in t and not c["website"]: c["website"] = u
            if r.get("totalAmount") or r.get("amount"): c["boost"] = max(c.get("boost") or 0, r.get("totalAmount") or r.get("amount") or 0)
            if r.get("description") and not c["desc"]: c["desc"] = str(r["description"])[:300]
def ingest_st(rows):
    for r in rows or []:
        t = r.get("token") or {}; m = t.get("mint")
        if not m: continue
        with L:
            c = cand(m, "st.trending"); rk = r.get("risk") or {}
            pc = lambda v: (v.get("totalPercentage") if isinstance(v, dict) else v) if isinstance(v, (dict, int, float)) else None
            c["st"] = dict(t=int(time.time()), rugged=bool(rk.get("rugged")), score=rk.get("score"),
                           snipers=pc(rk.get("snipers")), insiders=pc(rk.get("insiders")), bundlers=pc(rk.get("bundlers")),
                           risks=[x.get("name") for x in rk.get("risks") or [] if x.get("level") == "danger"][:5])
            c["sym"] = c["sym"] or t.get("symbol") or ""; c["name"] = c["name"] or t.get("name") or ""
            if t.get("twitter") and not c["twitter"]: c["twitter"] = t["twitter"]

# ------------------------------------------------------------------ discovery loop
PUMP = "https://frontend-api-v3.pump.fun"; JUP = "https://lite-api.jup.ag/tokens/v2"; DEX = "https://api.dexscreener.com"
SOURCES = [   # name, every_s, fn
    ("pump.new",    4,  lambda: ingest_pump(get(f"{PUMP}/coins?offset=0&limit=50&sort=created_timestamp&order=DESC&includeNsfw=false", "pump.new"), "pump.new")),
    ("pump.active", 15, lambda: ingest_pump(get(f"{PUMP}/coins?offset=0&limit=50&sort=last_trade_timestamp&order=DESC&includeNsfw=false&complete=false", "pump.active"), "pump.active")),
    ("pump.mcap",   30, lambda: ingest_pump(get(f"{PUMP}/coins?offset=0&limit=50&sort=market_cap&order=DESC&includeNsfw=false&complete=false", "pump.mcap"), "pump.mcap")),
    ("pump.live",   60, lambda: ingest_pump(get(f"{PUMP}/coins/currently-live?limit=50&offset=0&includeNsfw=false", "pump.live"), "pump.live")),
    ("jup.recent",  8,  lambda: ingest_jup(get(f"{JUP}/recent", "jup.recent"), "jup.recent")),
    ("jup.trending",60, lambda: ingest_jup(get(f"{JUP}/toptrending/5m?limit=50", "jup.trending"), "jup.trending")),
    ("jup.organic", 120,lambda: ingest_jup(get(f"{JUP}/toporganicscore/5m?limit=50", "jup.organic"), "jup.organic")),
    ("dex.profiles",30, lambda: ingest_dex(get(f"{DEX}/token-profiles/latest/v1", "dex.profiles"), "dex.profile")),
    ("dex.boosts",  30, lambda: ingest_dex(get(f"{DEX}/token-boosts/latest/v1", "dex.boosts"), "dex.boost")),
    ("dex.topboost",120,lambda: ingest_dex(get(f"{DEX}/token-boosts/top/v1", "dex.topboost"), "dex.boost")),
    ("dex.cto",     120,lambda: ingest_dex(get(f"{DEX}/community-takeovers/latest/v1", "dex.cto"), "dex.cto")),
]
def st_fetch():
    key = os.environ.get("SOLANATRACKER_API_KEY", "")
    if not key: HEALTH["st.trending"] = dict(ok=False, err="no SOLANATRACKER_API_KEY", nOk=0, nErr=0, lastOk=0, lastErr=0, fails=0); return
    ingest_st(get("https://data.solanatracker.io/tokens/trending/5m", "st.trending", headers={"x-api-key": key}, timeout=20))
def discovery():
    nxt = {"st": (ST.get("stLast") or 0) + float(H.get("st_every_min", 30)) * 60}   # restarts don't burn Solana Tracker credits
    st_every = float(H.get("st_every_min", 30)) * 60
    while True:
        now = time.time()
        for name, every, fn in SOURCES:
            if now >= nxt.get(name, 0):
                try: fn()
                except Exception as e: log("source", name, str(e)[:100])
                nxt[name] = time.time() + backoff(name, every)
        if now >= nxt.get("st", 0):
            ST["stLast"] = int(time.time())
            try: st_fetch()
            except Exception as e: log("source st", str(e)[:100])
            nxt["st"] = time.time() + (st_every if HEALTH.get("st.trending", {}).get("ok") else max(600, st_every / 3))
        prune(); time.sleep(1)

def prune():
    now = time.time(); maxage = float(H["max_age_h"]) * 3600
    with L:
        held = {p["mint"] for p in ST["positions"]}
        for m, c in list(POOL.items()):
            if m in held: continue
            age = now - (c["created"] or c["first"])
            mc = (c["j"] or {}).get("mcap") or (c["pump"] or {}).get("usd_market_cap") or 0
            if age > maxage + 3600 or (now - c["first"] > 1800 and c["snaps"] and mc < 5000 and max(s["mcap"] for s in c["snaps"]) < 8000):
                del POOL[m]
        if len(POOL) > 5000:
            for m, _ in sorted(POOL.items(), key=lambda kv: kv[1]["first"])[:len(POOL) - 5000]:
                if m not in held: POOL.pop(m, None)

# ------------------------------------------------------------------ enrichment (Jupiter tokens v2 batch search, free)
def enrich():
    while True:
        try:
            now = time.time(); maxage = float(H["max_age_h"]) * 3600
            with L:
                due = [c for c in POOL.values() if c["nextCheck"] <= now and now - (c["created"] or c["first"]) <= maxage]
                due.sort(key=lambda c: (c["nextCheck"], -c["first"]))
                batch = [c["mint"] for c in due[:30]]
            if batch:
                rows = get(f"{JUP}/search?query=" + ",".join(batch), "jup.search", timeout=15)
                got = set()
                with L:
                    for r in rows or []:
                        c = POOL.get(r.get("id"))
                        if not c: continue
                        got.add(c["mint"]); jupset(c, r)
                        s5 = r.get("stats5m") or {}; alive = (r.get("mcap") or 0) >= 7000 or (s5.get("numTraders") or 0) >= 8
                        c["fails"] = 0 if alive else c.get("fails", 0) + 1
                        c["nextCheck"] = now + (10 if alive else min(300, 30 * 2 ** c["fails"]))
                        score(c)
                    for m in batch:
                        if m not in got and m in POOL:
                            c = POOL[m]; c["miss"] = c.get("miss", 0) + 1; c["nextCheck"] = now + min(300, 20 * c["miss"])
            time.sleep(backoff("jup.search", 3))
        except Exception as e: log("enrich", str(e)[:120]); time.sleep(5)

# ------------------------------------------------------------------ X / social via fxtwitter (free, no auth, best effort)
XRE = re.compile(r"(?:x|twitter)\.com/(?:#!/)?([A-Za-z0-9_]{1,15})(?:/status(?:es)?/(\d+))?", re.I)
def xparse(u):
    if not u: return None
    if "/i/communities/" in u or "/communities/" in u: return dict(kind="community")
    m = XRE.search(u)
    if not m or m.group(1).lower() in ("i", "home", "search", "intent", "hashtag", "share"): return dict(kind="other")
    return dict(kind="status" if m.group(2) else "profile", handle=m.group(1), id=m.group(2))
def xlookup(info):
    key = info.get("id") or ("@" + info["handle"].lower())
    if key in XCACHE and time.time() - XCACHE[key]["t"] < 1800: return XCACHE[key]
    out = dict(t=int(time.time()), ok=False)
    if info["kind"] == "status":
        d = get(f"https://api.fxtwitter.com/status/{info['id']}", "x.fxtwitter")
        tw = (d or {}).get("tweet")
        if tw:
            a = tw.get("author") or {}
            out.update(ok=True, kind="status", handle=a.get("screen_name"), followers=a.get("followers"), joined=a.get("joined"),
                       verified=bool((a.get("verification") or {}).get("verified")), likes=tw.get("likes"), retweets=tw.get("retweets"),
                       replies=tw.get("replies"), views=tw.get("views"), tweetTs=tw.get("created_timestamp"), text=(tw.get("text") or "")[:200])
    else:
        for u in (f"https://api.fxtwitter.com/2/profile/{info['handle']}", f"https://api.fxtwitter.com/{info['handle']}"):
            d = get(u, "x.fxtwitter"); us = (d or {}).get("user")
            if us:
                out.update(ok=True, kind="profile", handle=us.get("screen_name"), followers=us.get("followers"), joined=us.get("joined"),
                           verified=bool((us.get("verification") or {}).get("verified")), tweets=us.get("tweets")); break
            time.sleep(1.2)
    XCACHE[key] = out
    if len(XCACHE) > 3000:
        for k in sorted(XCACHE, key=lambda k: XCACHE[k]["t"])[:1000]: XCACHE.pop(k, None)
    return out
def social_worker():
    while True:
        m = SOCQ.get()
        try:
            with L: c = POOL.get(m); info = xparse(c["twitter"]) if c else None
            if c and info:
                res = xlookup(info) if info["kind"] in ("status", "profile") else dict(ok=False, kind=info["kind"], t=int(time.time()))
                with L: c["x"] = res; score(c)
        except Exception as e: log("social", str(e)[:100])
        finally: SOC_PENDING.discard(m); time.sleep(1.5)

def joined_days(s):
    try: return (time.time() - datetime.datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").timestamp()) / 86400
    except Exception: return None

# ------------------------------------------------------------------ hot metas (words many fresh launches share = a live narrative)
STOPW = set("the and for coin token sol solana pump fun meme with this that you are not new official inu ai of on in to a is it my".split())
META = {"words": [], "t": 0}
def metas():
    if time.time() - META["t"] < 30: return META["words"]
    cnt = {}; now = time.time()
    with L:
        for c in POOL.values():
            if now - (c["created"] or c["first"]) > 1800 or "pump.new" not in c["src"]: continue
            ws = set(re.findall(r"[a-z]{4,}", (c["name"] + " " + c["sym"]).lower())) - STOPW
            for w in ws: cnt[w] = cnt.get(w, 0) + 1
    META.update(t=now, words=sorted([[w, n] for w, n in cnt.items() if n >= 5], key=lambda x: -x[1])[:12])
    return META["words"]
def kwhit(text, kws):
    t = text.lower()
    return [k for k in kws if re.search(r"(?<![a-z0-9])" + re.escape(k.lower()) + r"(?![a-z0-9])", t)]

# ------------------------------------------------------------------ hype score (0-100) — every point comes from a real field
def tier(v, steps):
    pts = 0
    for th, p in steps:
        if (v or 0) >= th: pts = p
    return pts
def score(c):
    j = c["j"] or {}; s5 = j.get("stats5m") or {}; a = j.get("audit") or {}; bd = []; cats = dict(social=0, narrative=0, traction=0, momentum=0, attention=0, penalty=0)
    def add(cat, label, p):
        if p: bd.append([cat, label, p]); cats[cat] += p
    # social (cap 25)
    x = c.get("x") or {}
    if c["twitter"]: add("social", "X link", 3)
    if c["website"]: add("social", "website", 1)
    if c["telegram"]: add("social", "telegram", 1)
    if x.get("ok"):
        f = x.get("followers") or 0
        add("social", f"@{x.get('handle')} {kfmt(f)} followers", tier(f, [(1e3, 4), (1e4, 8), (1e5, 12), (1e6, 16)]))
        if x["kind"] == "status":
            add("social", f"tweet {kfmt(x.get('views') or 0)} views", tier(x.get("views"), [(1e4, 3), (1e5, 5)]))
            add("social", f"tweet {kfmt(x.get('likes') or 0)} likes", tier(x.get("likes"), [(100, 3), (1000, 5)]))
        jd = joined_days(x.get("joined") or "")
        if jd is not None and x["kind"] == "profile":
            if jd < 30: add("social", f"X account only {jd:.0f}d old", -4)
            elif jd > 365: add("social", "X account >1y old", 3)
    elif (x.get("kind") == "community"): add("social", "X community", 2)
    cats["social"] = min(cats["social"], 25)
    # narrative (cap 20): watched accounts, keywords, auto-detected hot metas
    handles = {h.lower() for h in H.get("hype_handles") or []}
    xi = xparse(c["twitter"]) or {}
    hh = (x.get("handle") or xi.get("handle") or "").lower()
    if hh and hh in handles: add("narrative", f"linked to watched @{hh}", 15)
    kw = kwhit(f"{c['name']} {c['sym']} {c['desc']}", H.get("hype_keywords") or [])
    if kw: add("narrative", "keyword " + ", ".join(kw[:3]), 10)
    hot = {w for w, n in metas()}
    hw = (set(re.findall(r"[a-z]{4,}", (c["name"] + " " + c["sym"]).lower())) - STOPW) & hot
    if hw: add("narrative", "hot meta: " + ", ".join(sorted(hw)[:2]), 8)
    cats["narrative"] = min(cats["narrative"], 20)
    # traction (cap 40) — Jupiter 5-minute stats
    add("traction", f"{s5.get('numTraders') or 0} traders/5m", tier(s5.get("numTraders"), [(20, 5), (50, 10), (100, 14), (200, 18)]))
    add("traction", f"${kfmt(s5.get('buyVolume') or 0)} bought/5m", tier(s5.get("buyVolume"), [(3e3, 4), (1e4, 7), (3e4, 10)]))
    add("traction", f"{s5.get('numNetBuyers') or 0} net buyers/5m", tier(s5.get("numNetBuyers"), [(10, 3), (30, 5)]))
    add("traction", f"{j.get('holderCount') or 0} holders", tier(j.get("holderCount"), [(50, 2), (150, 4), (400, 6)]))
    add("traction", f"organic score {j.get('organicScore') or 0:.0f}", tier(j.get("organicScore"), [(20, 2), (50, 4)]))
    bv, sv = s5.get("buyVolume") or 0, s5.get("sellVolume") or 0
    if sv > 2000 and bv / sv < 0.8: add("traction", f"sellers winning (buy/sell {bv/sv:.2f})", -4)
    cats["traction"] = min(cats["traction"], 40)
    # momentum (cap 15)
    sn = c["snaps"]; first = next((s for s in sn if s["mcap"]), None)
    if first and sn[-1]["t"] - first["t"] >= 60 and first["mcap"]:
        g = sn[-1]["mcap"] / first["mcap"] - 1
        add("momentum", f"mcap {g*100:+.0f}% since seen", tier(g, [(0.5, 5), (1.5, 10), (4, 12)]))
        hg = sn[-1]["holders"] - first["holders"]
        add("momentum", f"+{hg} holders since seen", tier(hg, [(20, 3)]))
    add("momentum", f"price {s5.get('priceChange') or 0:+.0f}%/5m", tier(s5.get("priceChange"), [(15, 3)]))
    if (c["pump"] or {}).get("king_of_the_hill_timestamp"): add("momentum", "pump.fun king of the hill", 4)
    if (c["pump"] or {}).get("is_currently_live"): add("momentum", "live stream on pump.fun", 3)
    cats["momentum"] = min(cats["momentum"], 15)
    # attention (cap 12)
    sr = c["src"]
    if "dex.profile" in sr: add("attention", "DexScreener profile paid", 4)
    if "dex.boost" in sr: add("attention", f"DexScreener boost {c.get('boost') or ''}".strip(), 3)
    if "dex.cto" in sr: add("attention", "community takeover", 2)
    if "jup.trending" in sr: add("attention", "Jupiter top trending 5m", 5)
    if "st.trending" in sr: add("attention", "Solana Tracker trending", 5)
    cats["attention"] = min(cats["attention"], 12)
    # penalties
    dm, dg = a.get("devMints") or 0, a.get("devMigrations") or 0
    if dm >= 50 and dg / dm < 0.02: add("penalty", f"serial launcher dev ({dm} mints, {dg} migrated)", -5)
    if (a.get("devBalancePercentage") or 0) > 5: add("penalty", f"dev holds {a['devBalancePercentage']:.1f}%", -3)
    st = c.get("st") or {}
    for k, lab in (("bundlers", "bundlers"), ("snipers", "snipers"), ("insiders", "insiders")):
        if (st.get(k) or 0) > 20: add("penalty", f"{lab} {st[k]:.0f}%", -5)
    tot = max(0, min(100, sum(cats.values())))
    c["score"], c["cats"], c["bd"] = tot, cats, bd
    # social lookup is worth doing once the non-social part is promising
    if c["twitter"] and c.get("x") is None and c["mint"] not in SOC_PENDING and tot >= float(H["min_score"]) - 25:
        SOC_PENDING.add(c["mint"]); SOCQ.put(c["mint"])
    return tot
def kfmt(v):
    v = float(v or 0)
    return f"{v/1e6:.1f}M" if v >= 1e6 else f"{v/1e3:.1f}k" if v >= 1e3 else f"{v:.0f}"

# ------------------------------------------------------------------ gates
def cheap_gates(c):
    j = c["j"] or {}; s5 = j.get("stats5m") or {}; a = j.get("audit") or {}; now = time.time()
    age = now - (c["created"] or c["first"])
    if age < float(H["min_age_s"]): return f"too new ({age:.0f}s)"
    if age > float(H["max_age_h"]) * 3600: return f"too old ({age/3600:.1f}h)"
    mc = j.get("mcap") or 0
    if mc < float(H["min_mcap"]): return f"mcap ${kfmt(mc)} < ${kfmt(H['min_mcap'])}"
    if mc > float(H["max_mcap"]): return f"mcap ${kfmt(mc)} > ${kfmt(H['max_mcap'])} (not early)"
    if (s5.get("numTraders") or 0) < float(H["min_traders_5m"]): return f"{s5.get('numTraders') or 0} traders/5m < {H['min_traders_5m']}"
    if (j.get("liquidity") or 0) < float(H["min_liq"]): return f"liquidity ${kfmt(j.get('liquidity') or 0)} < ${kfmt(H['min_liq'])}"
    if a.get("mintAuthorityDisabled") is False: return "mint authority ON"
    if a.get("freezeAuthorityDisabled") is False: return "freeze authority ON"
    if (a.get("devBalancePercentage") or 0) > float(H["max_dev_pct"]): return f"dev holds {a['devBalancePercentage']:.1f}% > {H['max_dev_pct']}%"
    if (c.get("st") or {}).get("rugged"): return "Solana Tracker flags it rugged"
    return None
def copycat(c):
    """Same ticker (or same linked tweet) as a bigger coin = copycat of the real one."""
    sym = re.sub(r"[^A-Z0-9]", "", (c["sym"] or "").upper()); mc = (c["j"] or {}).get("mcap") or 0
    if not sym: return None
    ratio = float(H["copycat_ratio"])
    with L:
        for o in POOL.values():
            if o is c: continue
            omc = (o["j"] or {}).get("mcap") or 0
            same = re.sub(r"[^A-Z0-9]", "", (o["sym"] or "").upper()) == sym or (c["twitter"] and o["twitter"] == c["twitter"])
            if same and omc >= ratio * mc: return f"copycat: {o['sym']} {o['mint'][:4]}… already at ${kfmt(omc)} (ours ${kfmt(mc)})"
    d = get(f"{DEX}/latest/dex/search?q=" + urllib.parse.quote(sym), "dex.search")
    if d is None: return "duplicate-ticker check unavailable (DexScreener search failed)"
    for p in d.get("pairs") or []:
        b = p.get("baseToken") or {}
        if p.get("chainId") == "solana" and b.get("address") != c["mint"] and re.sub(r"[^A-Z0-9]", "", (b.get("symbol") or "").upper()) == sym \
           and (p.get("marketCap") or 0) >= ratio * mc:
            return f"copycat: older ${b.get('symbol')} {b['address'][:4]}… at ${kfmt(p.get('marketCap'))} (ours ${kfmt(mc)})"
    return None
def chain_gate(c):
    try: f = safety.chain_facts(c["mint"])
    except Exception as e: return f"on-chain check failed ({core.redact(str(e))[:60]})", None
    if f.get("mintAuth"): return "mint authority ON (on-chain)", f
    if f.get("freezeAuth"): return "freeze authority ON (on-chain)", f
    if f.get("badExt"): return "token-2022 trap: " + ", ".join(map(str, f["badExt"])), f
    if f.get("top10Pct", 0) > float(H["max_top10_pct"]): return f"top-10 wallets hold {f['top10Pct']:.0f}% > {H['max_top10_pct']}%", f
    if f.get("topPct", 0) > float(H["max_top1_pct"]): return f"largest wallet holds {f['topPct']:.0f}% > {H['max_top1_pct']}%", f
    return None, f

# ------------------------------------------------------------------ fills (same model as copier2: real Jupiter route quote + fees)
def quote_buy(mint, usd, dec):
    q = core.jup_quote(WSOL, mint, int(usd / SOL[0] * 1e9), 1000)
    if not q: return None, None
    out = int(q["outAmount"]) / 10 ** dec
    return (usd / out if out > 0 else None), float(q.get("priceImpactPct") or 0) * 100
def quote_sell(mint, qty, dec):
    raw = int(qty * 10 ** dec)
    q = core.jup_quote(mint, WSOL, raw, 1000) if raw > 0 else None
    if not q: return None
    return int(q["outAmount"]) / 1e9 * SOL[0] / qty
def jup_price(mints):
    out = {}
    for i in range(0, len(mints), 50):
        r = get("https://lite-api.jup.ag/price/v3?ids=" + ",".join(mints[i:i + 50]), "jup.price", timeout=8) or {}
        for m, v in r.items():
            if v and v.get("usdPrice"): out[m] = float(v["usdPrice"])
    return out

# ------------------------------------------------------------------ state
def fresh():
    now = int(time.time()); s0 = float(H["start_usd"])
    return dict(version=1, strategy="hype", paper=True, started=now, cash=s0, startUsd=s0, positions=[], closes=[], decisions=[],
                equity=[[now, s0]], last_poll=0, stats=dict(seen=0, scored=0, deep=0, entered=0, skipped=0))
def load():
    try:
        s = json.load(open(SF))
        if s.get("strategy") == "hype": return s
    except Exception: pass
    return fresh()
ST = load()
def equity(s): return s["cash"] + sum(p["qty"] * p["last"] * (1 - SLIP) for p in s["positions"])
def save():
    with L:
        s = ST; now = int(time.time()); s["last_poll"] = now
        s["cfg"] = {k: v for k, v in H.items()}; s["sources"] = {k: {kk: v[kk] for kk in ("ok", "nOk", "nErr", "lastOk", "lastErr", "err")} for k, v in HEALTH.items()}
        s["metas"] = metas(); s["solUsd"] = SOL[0]; s["pool"] = len(POOL)
        live = [c for c in POOL.values() if c["score"] is not None and c["j"]]
        s["stats"]["scored"] = len(live)
        live.sort(key=lambda c: -(c["score"] or 0))
        s["candidates"] = [card(c) for c in live[:30]]
        blob = json.dumps(s, separators=(",", ":"))
    tmp = SF + ".tmp"; open(tmp, "w").write(blob); os.replace(tmp, SF)
def card(c):
    j = c["j"] or {}; s5 = j.get("stats5m") or {}; x = c.get("x") or {}
    return dict(mint=c["mint"], sym=c["sym"], name=c["name"][:40], icon=c["icon"], score=c["score"], cats=c["cats"], bd=c["bd"],
                age=int(time.time() - (c["created"] or c["first"])), mcap=round(j.get("mcap") or 0), liq=round(j.get("liquidity") or 0),
                holders=j.get("holderCount"), traders5m=s5.get("numTraders"), buy5m=round(s5.get("buyVolume") or 0), sell5m=round(s5.get("sellVolume") or 0),
                chg5m=s5.get("priceChange"), launchpad=j.get("launchpad"), sources=sorted(c["src"]), twitter=c["twitter"], website=c["website"],
                telegram=c["telegram"], x={k: x.get(k) for k in ("ok", "kind", "handle", "followers", "views", "likes", "verified")} if x else None,
                status=c["status"], why=c.get("why", ""))
def decide(c, action, reason, extra=None):
    d = dict(ts=int(time.time()), action=action, reason=reason, mint=c["mint"], sym=c["sym"], name=c["name"][:40], icon=c["icon"],
             score=c["score"], cats=c["cats"], bd=c["bd"], mcap=round((c["j"] or {}).get("mcap") or 0), traders5m=((c["j"] or {}).get("stats5m") or {}).get("numTraders"),
             x={k: (c.get("x") or {}).get(k) for k in ("handle", "followers", "kind")} if c.get("x") else None, twitter=c["twitter"])
    if extra: d.update(extra)
    ST["decisions"].insert(0, d); del ST["decisions"][150:]
    ST["stats"]["entered" if action == "enter" else "skipped"] += 1
    c["status"] = "entered" if action == "enter" else "skipped"; c["why"] = reason; c["lastDecision"] = time.time()
    log(action.upper(), c["sym"] or c["mint"][:6], f"score {c['score']}", "|", reason)

# ------------------------------------------------------------------ trader: evaluate, enter, mark, exit
def evaluate():
    ms = float(H["min_score"])
    with L:
        held = {p["mint"] for p in ST["positions"]}; done = {d["mint"] for d in ST["decisions"] if d["action"] == "enter"} | {x["mint"] for x in ST["closes"]}
        cs = [c for c in POOL.values() if (c["score"] or 0) >= ms and c["mint"] not in held and c["mint"] not in done
              and time.time() - c["lastDecision"] > (600 if c["status"] == "skipped" else 0) and c["mint"] not in SOC_PENDING]
        cs.sort(key=lambda c: -c["score"])
    for c in cs[:4]:
        with L:
            if len(ST["positions"]) >= int(H["max_open"]): decide(c, "skip", f"max {H['max_open']} open positions"); continue
            if ST["cash"] < float(H["ticket_usd"]): decide(c, "skip", f"cash ${ST['cash']:.2f} < ticket"); continue
            why = cheap_gates(c)
        if why: 
            with L: decide(c, "skip", why)
            continue
        ST["stats"]["deep"] = ST["stats"].get("deep", 0) + 1
        why = copycat(c)
        if why:
            with L: decide(c, "skip", why)
            continue
        why, facts = chain_gate(c)
        if why:
            with L: decide(c, "skip", why, dict(chain=facts))
            continue
        enter(c, facts)
def enter(c, facts):
    tk = float(H["ticket_usd"]); dec = int((c["j"] or {}).get("decimals") or 6)
    if SOL[0] <= 0:
        with L: decide(c, "skip", "no SOL price"); return
    px, imp = quote_buy(c["mint"], tk, dec)
    if not px:
        with L: decide(c, "skip", "no Jupiter buy route", dict(chain=facts)); return
    qty = max(0.0, tk - PRIO_FEE_SOL * SOL[0]) * (1 - LAND_SLIP) / px
    back = quote_sell(c["mint"], qty, dec)
    if not back:
        with L: decide(c, "skip", "no Jupiter sell route (honeypot?)", dict(chain=facts)); return
    rt = (1 - (qty * back * (1 - LAND_SLIP) - PRIO_FEE_SOL * SOL[0]) / tk) * 100
    if rt > float(H["max_roundtrip_loss_pct"]):
        with L: decide(c, "skip", f"round-trip cost {rt:.0f}% > {H['max_roundtrip_loss_pct']}% (too thin)", dict(chain=facts)); return
    now = int(time.time())
    with L:
        p = dict(mint=c["mint"], sym=c["sym"], name=c["name"][:40], icon=c["icon"], dec=dec, entry=px, qty=qty, cost=tk, cost0=tk, opened=now,
                 last=px, peak=px, tpDone=False, realized=0.0, score=c["score"], cats=c["cats"], bd=c["bd"], impactPct=round(imp or 0, 2),
                 roundTripPct=round(rt, 1), entryMcap=round((c["j"] or {}).get("mcap") or 0), chain=facts, twitter=c["twitter"], website=c["website"],
                 telegram=c["telegram"], x={k: (c.get("x") or {}).get(k) for k in ("handle", "followers", "kind", "views", "likes")} if c.get("x") else None,
                 hist=[[now, px]], noquote_since=None, sells=[])
        ST["cash"] -= tk; ST["positions"].append(p)
        decide(c, "enter", f"score {c['score']} >= {H['min_score']}, all gates passed; filled ${tk:.0f} at Jupiter quote (impact {imp:.1f}%, round-trip {rt:.1f}%)",
               dict(chain=facts, entry=px))
def sell(p, frac, why):
    frac = max(0.0, min(1.0, frac)); q = p["qty"] * frac; cst = p["cost"] * frac
    qpx = None
    try: qpx = quote_sell(p["mint"], q, p.get("dec", 6))
    except Exception as e: log("quote sell", str(e)[:80])
    if qpx: px = qpx; proceeds = max(0.0, q * px * (1 - LAND_SLIP) - PRIO_FEE_SOL * SOL[0]); src = "jupiter"
    else: px = p["last"]; proceeds = q * px * (1 - SLIP); src = "mark-2%"
    ST["cash"] += proceeds; p["qty"] -= q; p["cost"] -= cst; p["realized"] += proceeds - cst
    p["sells"].append(dict(ts=int(time.time()), frac=round(frac, 3), px=px, usd=round(proceeds, 4), why=why, fill=src))
    log("SELL", p["sym"], f"{frac*100:.0f}%", why, f"${proceeds:.2f}")
    if frac >= 0.999 or p["qty"] * px < 0.01:
        ST["positions"].remove(p)
        ST["closes"].insert(0, dict(mint=p["mint"], sym=p["sym"], name=p["name"], icon=p["icon"], opened=p["opened"], closed=int(time.time()),
                                    entry=p["entry"], exit=px, peak=p["peak"], cost=p["cost0"], pnl=round(p["realized"], 4),
                                    pct=round(p["realized"] / p["cost0"] * 100, 1), why=why, score=p["score"], cats=p["cats"], sells=p["sells"]))
        del ST["closes"][300:]
def exits():
    now = time.time(); tp, frac, trail = float(H["tp_mult"]), float(H["tp_frac"]), float(H["trail_pct"])
    for p in list(ST["positions"]):
        px, e = p["last"], p["entry"]
        if p.get("noquote_since") and now - p["noquote_since"] > float(H["noquote_min"]) * 60:
            sell(p, 1.0, f"emergency: no price for {H['noquote_min']} min"); continue
        if px <= 0: continue
        if px <= e * (1 - float(H["stop_pct"]) / 100): sell(p, 1.0, f"hard stop (-{H['stop_pct']}%)"); continue
        if now - p["opened"] > float(H["max_hold_h"]) * 3600: sell(p, 1.0, f"max hold {H['max_hold_h']}h"); continue
        if not p["tpDone"] and px >= e * tp:
            sell(p, frac, f"profit lock: sold half at {tp:g}x"); p["tpDone"] = True
            if p not in ST["positions"]: continue
        if p["tpDone"]:
            p["peak"] = max(p["peak"], px); p["trailPx"] = p["peak"] * (1 - trail / 100)
            if px <= p["trailPx"]: sell(p, 1.0, f"trailing stop (-{trail:g}% from peak)")
def mark():
    with L: mints = [p["mint"] for p in ST["positions"]]
    jp = jup_price(mints) if mints else {}
    miss = [m for m in mints if m not in jp]
    dx = core.dexscreener_info(miss) if miss else {}
    with L:
        now = int(time.time())
        for p in ST["positions"]:
            px = jp.get(p["mint"]) or (dx.get(p["mint"]) or {}).get("price_usd")
            if px:
                p["last"] = px; p["peak"] = max(p["peak"], px); p["noquote_since"] = None
                if not p["hist"] or now - p["hist"][-1][0] >= 30: p["hist"].append([now, px]); del p["hist"][:-240]
                c = POOL.get(p["mint"])
                if c: c["nextCheck"] = min(c["nextCheck"], now + 30)
            elif not p.get("noquote_since"): p["noquote_since"] = now
        exits()
        eq = equity(ST)
        if not ST["equity"] or now - ST["equity"][-1][0] >= 30: ST["equity"].append([now, round(eq, 4)])
        if len(ST["equity"]) > 4000: ST["equity"] = ST["equity"][::2]
def trader():
    last_sol = last_cfg = 0
    while True:
        try:
            if time.time() - last_cfg > 60: H.clear(); H.update(cfg()); last_cfg = time.time()   # hype_cfg.json edits apply live
            if time.time() - last_sol > 60:
                try:
                    v = core.sol_price()
                    if v: SOL[0] = v
                except Exception: pass
                if not SOL[0]: SOL[0] = jup_price([WSOL]).get(WSOL, 0.0)
                last_sol = time.time()
            mark(); evaluate(); save()
        except Exception as e: log("trader", str(e)[:150], traceback.format_exc()[-300:])
        time.sleep(8)

def main():
    log(f"PAPER hype sniper: cash ${ST['cash']:.2f}, {len(ST['positions'])} open, min score {H['min_score']}, ticket ${H['ticket_usd']}")
    for fn in (discovery, enrich, social_worker): threading.Thread(target=fn, daemon=True).start()
    trader()
if __name__ == "__main__": main()
