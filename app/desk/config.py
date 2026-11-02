# config.py · every number lives here (PAPER ONLY: this desk never connects a wallet, holds a key, or signs)
PAPER           = True
START_USD       = 100.0   # fake bankroll
TICKET_USD      = 10      # backtest ticket (article)
LIVE_TICKET_USD = 7       # live paper ticket, scaled to a $100 bankroll
MAX_PER_COIN    = 30      # live copy stops adding to a coin at $30
LOOKBACK_DAYS   = 30
MAX_PAGES       = 15      # Helius getTransactionsForAddress pages (100 tx each) per wallet in the backtest
MIN_BUYS        = 10
MIN_COPY_X      = 1.5
MAX_SHARE_ONE   = 0.70
MIN_HOLD_SEC    = 60
MIN_COIN_AGE_S  = 120
MIN_LIQ_USD     = 25_000
MIN_VOL24_USD   = 50_000
PUMP_ATH_WATCH  = 0.05    # pump.fun coin at <5% of ATH market cap after 1h = skip
MAX_SHORTLIST   = 10
POLL_SEC        = 30      # live: check each shortlisted wallet's new signatures
COPY_DELAY_S    = 3       # simulated copy-bot latency after we detect the buy
SLIP_FEE_SIDE   = 0.02    # 2% slippage+fees on every paper buy and sell
MAX_SIGNAL_AGE_S= 180     # ignore leader buys older than this when detected

# ---- v2 scout (robust) ----
HELIUS_RPS = 8; HELIUS_THREADS = 8
STAGE1_N = 140; STAGE1_PAGES = 10          # 1,000 tx for the best 140 after a 1-page prescreen
STAGE2_N = 40;  STAGE2_PAGES = 50          # 5,000 tx (was 1,500) for the best 40
GECKO_PAGES = 2; GECKO_MAX = 60
DELAY_PEN = 0.01                            # extra adverse price per side from the copy delay (calibrated in backtest.py)
MIN_COINS = 8; MIN_X_REAL = 1.2; MIN_X_EX_BEST = 1.1; MIN_PF = 1.3
SCOUT_MIN_X_EX_BEST = 0.95; SCOUT_MIN_PF = 1.0; MAX_SCOUTS = 12

# ---- v2 coin safety ----
TOP1_MAX_PCT = 20; TOP10_MAX_PCT = 50; SAFETY_CACHE_S = 600

# ---- v2 live copier ----
DETECT = "ws"                 # Helius standard websockets (logsSubscribe per wallet); backup polling below
BACKUP_POLL_SEC = 90          # getSignaturesForAddress catch-up per wallet (1 credit each)
MARK_SEC = 8                  # DexScreener price refresh for exits / equity
CONSENSUS_N = 2; CONSENSUS_WIN_S = 900; BOOST_X = 1.5; BOOST_X3 = 2.0   # 2+ wallets same coin within 15 min -> bigger ticket
SCOUTS_NEED_CONSENSUS = True  # scout-tier wallets only trigger entries as part of a consensus
EXIT_MODE = "hybrid"          # "mirror" | "risk" | "hybrid" (mirror sells + risk exits) -- set from backtest.py
STOP_LOSS = 0.40; TP1_X = 2.0; TP1_FRAC = 0.5; TRAIL = 0.30; MAX_HOLD_MIN = 360
MAX_OPEN = 8; DAY_LOSS_PCT = 0.15
# v2 data hygiene: ignore leader buys we could never copy at his price (dust, or pump.fun prices below the bonding-curve start = parse junk/airdrop)
MIN_COPY_SOL = 0.05
PUMP_MIN_PX_SOL = 2.0e-8
# v2 exit rule chosen by backtest.py (backtest_exits.json): hybrid-30/2x/25
EXIT_MODE = "hybrid"; STOP_LOSS = 0.30; TP1_X = 2.0; TP1_FRAC = 0.5; TRAIL = 0.25; MAX_HOLD_MIN = 360
# v3 (research audit, 1 Nov 2026): honest delay cost, reachability gate, wallet hygiene gates (GMGN wallet-analysis gates G1-G4, DexTrading checks)
DELAY_PEN = 0.056            # was 0.01: calibrated in backtest.py on minute candles (median move against us over the copy delay)
PUMP_MIN_ENTRY_MCAP_USD = 30_000   # pump.fun leader entries below ~$30k mcap are bonding-curve snipes we cannot reach at his price (GMGN G3)
MAX_IDLE_H = 48              # dormant 48h+ = drop (DexTrading)
MAX_BUYS_PER_DAY = 100       # >100 trades/day = bot / unreachable (GMGN G3)
MIN_X7 = 0.85                # last-7-day copy result must not have collapsed (GMGN G2: currency)
MAX_ZERO_SHARE = 0.50        # >50% of his coins now worth <10% of his entry = exit liquidity / rug magnet (G4; flag from 35%)
EXEC_DELAY_S = 1.5           # simulated execution after detection (consumer bot on public RPC: 0.5-2.5s; we fill at the real pool price at that moment)
COPY_ADDS = "winners"        # leader adds: "all" | "winners" (only add while our lot is green, DexTrading) | "none"
PRIO_FEE_SOL = 0.001         # per simulated tx: base + priority fee (GMGN guide: 0.002-0.006 SOL is common; we model a lean 0.001)
LAND_SLIP = 0.005            # extra move between quote and landing
FILL_SOURCE = "jupiter"      # paper fills = Jupiter executable quote for OUR size at the moment we would send (includes pool fee + price impact)
# v3 exits (backtest on shortlist3): pure mirror scored highest ($522 vs $503) but has no stop; we run mirror + -40% catastrophe stop
EXIT_MODE = "hybrid"; STOP_LOSS = 0.40; TP1_X = 1e9; TP1_FRAC = 0.5; TRAIL = 0.30; MAX_HOLD_MIN = 10**7
# v3.1 detection hardening
BACKUP_POLL_SEC = 30          # catch-up poll per wallet (1 credit each): ~17k credits/day for 6 wallets
WS_PING_SEC = 10              # our own ping; no pong in 10s -> reconnect
FREEZE_GAP_S = 20             # wall clock jumped this much more than monotonic -> box was frozen: reconnect + poll now
FEED_ALERT_POLL_S = 120       # no successful poll this long -> feed alert
# Profit lock on top of MIRROR (paper sells only): sell half at 2x, then trail the rest 30% under the high.
TP_MULT = 2.0        # mark >= entry * tp_mult arms the partial
TP_FRAC = 0.5        # fraction of the qty still open to paper-sell
TRAIL_PCT = 30       # after that partial, exit the remainder this far under the peak
# ---- v4 MIRROR mode (Mohamed 12:29 Nov 1): copy every real leader buy/sell immediately, no liquidity/volume/age/launch-curve gates. PAPER ONLY.
MIRROR = True
if MIRROR:
    MIRROR_TICKET_CORE = 7; MIRROR_TICKET_SCOUT = 5   # fixed $ tickets (consensus boost x1.5/x2 still applies, capped per coin)
    MAX_OPEN = 8                                      # per-coin cap stays MAX_PER_COIN ($30); max 8 coins open
    DAY_LOSS_PCT = 0.40                               # daily halt only at -40% of day-start equity
    EXIT_MODE = "mirror"; COPY_ADDS = "all"           # exits = leader sells only; copy his adds too (up to the coin cap)
    SCOUTS_NEED_CONSENSUS = False
    MIRROR_MAX_HOLD_H = 24                            # long safety net: close anything held 24h
    MIRROR_NOQUOTE_MIN = 30                           # emergency: no price for 30 min -> exit at Jupiter quote, else write off
    STOP_LOSS = 1.0; TP1_X = 1e9; TRAIL = 1.0; MAX_HOLD_MIN = MIRROR_MAX_HOLD_H * 60
else:
    MIRROR_TICKET_CORE = MIRROR_TICKET_SCOUT = 0; MIRROR_MAX_HOLD_H = 0; MIRROR_NOQUOTE_MIN = 0
DROP_WALLETS = ["7aMgK5L4qEQ8Nyv6ZzhZi2B82NSSRnwb2NGJnNagA46D"]   # dropped from the shortlist at 3:14 (parent); not watched
# ---- v5 (Mohamed t29u, 1 Nov 11:22 PM): orphan guard + quick-flip sizing. PAPER ONLY.
# Quick flips: a round trip = leader's first buy of a coin -> his first sell of it. Our detect + fill is ~3.4 s, so a leader who
# is out within ~20 s leaves us buying his top and selling after him. 1 Nov evening: all 6 of Numer0's round trips under 15 s lost
# money for us, his 72 s+ trades made the day (PEPE2 +$12.8). So we shrink, not skip: skipping him would also have skipped PEPE2.
FLIP_HOLD_S = 20          # a round trip shorter than this counts as a quick flip
FLIP_LOOKBACK = 6         # judge on the trader's last N completed round trips (any coin, copied or not)
FLIP_MIN_N = 4            # need at least this many round trips before the rule kicks in
FLIP_SHARE = 0.5          # rule on when >= this share of those round trips are quick flips
FLIP_ACTION = "reduce"    # "reduce" (ticket x FLIP_MULT) | "skip" (don't copy while flipping) | "off"
FLIP_MULT = 0.5
ORPHAN_SWEEP_V = 1        # one-time startup cleanup: exit lots whose leader already sold everything (bump to re-run once)
