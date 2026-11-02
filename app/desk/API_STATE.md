# GET /api/state  (JSON, PAPER MODE ONLY)
Poll every ~4s; send header `ngrok-skip-browser-warning: 1` through ngrok. Fields marked **NEW** are added in v2 and
may be missing/null on older state — render defensively (`x?.y ?? fallback`). Existing fields are never removed or renamed.
Times: `*Ts` = unix seconds; `at`/`started`/`asOf` = preformatted "1:48 AM EST" strings. Money in USD unless noted.

## Top level
| field | type | meaning |
|---|---|---|
| mode | "desk" | always "desk" |
| version | int **NEW** | 2 |
| asOf | str | server time of this snapshot |
| live | bool | false = copier not started yet (only backtest fields present) |
| momentum | obj/null | archived momentum bot: {final, trades, wins, realized, open, started, ended} |
| prevSessions | list **NEW** | archived copy-desk sessions: [{file, started, ended, final, closes}] |
| backtest | obj | {generated, candidates, tested, passed, credits, sol, greenCopies, withBuys} + **NEW** {sources:[str], txCap, scouts} |
| shortlist | list | core traders the desk copies (see Trader row) |
| scouts | list **NEW** | near-cutoff wallets used ONLY for consensus entries (same Trader row shape, tier="scout") |
| near | list | dropped wallets: [{name, x, buys, why}] |
| strategy | obj **NEW** | see Strategy |
| risk | obj **NEW** | {dayPnl, dayLossLimit, halted(bool), haltReason, openN, maxOpen, perCoinCap, cash} |
| detect | obj **NEW** | {mode:"ws"\|"poll", pollSec, lastEventTs, medianLagS, wsConnected(bool)} |
| traders | list **NEW** | live per-wallet stats: [{name, addr, tier, signals, copied, skipped, pnl, lastSeenTs, lastSeen}] |
| startTs, started, lastTs, polls, cash, credits, signals, skippedN, closesN | | session stats (credits = Helius credits used this session) |
| equity | [[ts, usd], ...] | downsampled to ≤300 points |
| watching | [str] | names of wallets being watched (core + scouts) |
| cfg | obj | {ticket, max_coin, slip, delay, poll} + **NEW** {stopLoss, tp1X, tp1Frac, trail, maxHoldMin, maxOpen, dayLoss, consensusN, consensusWinS, boostX} |
| positions | list | open copies (see Position) |
| closes | list | last 30 closed/partial sells (see Close) |
| skipped | list | last 25 skipped signals (see Skip) |

## Trader row (shortlist / scouts)
Existing: name, addr, buys, coins, put, got, x (raw copy multiple), bpd (buys/day), hold (median hold s), days (history window), flags[str], best (best coin symbol), bestUsd.
**NEW**: tier ("core"|"scout"), score (robust rank score, higher better), xReal (multiple after delay+2%/side), xExBest (real multiple excluding best coin),
winRate (0-1, per coin), pf (profit factor), greenDays (share of active days positive 0-1), activeDays, xRisk (multiple with chosen exit rules), txs (tx fetched).

## Position (open copy)
Existing: addr (mint), qty, sym, name, logo, url, wallet (name of copied wallet), held (min), cost, value, pnl, pct, entry, last, move (% vs entry), buys, lag (leader signal age s), spark.
**NEW**: reason {type:"mirror"\|"consensus", wallets:[names], consensus:int, ticket}, safety (see Safety), peak (best price seen), tpDone (bool, 50% taken),
stopPx, trailPx, openedTs.

## Close
Existing: sym, name, logo, url, wallet, at, held (min), entry, exit, cost, pnl, pct, share (% of position sold).
**NEW**: exitReason ("mirror sell"|"stop loss"|"take profit"|"trailing stop"|"max hold"|"daily halt"), reason (entry reason obj as above), ts.

## Skip
Existing: at, wallet, sym, reason (human string), logo, url.
**NEW**: kind ("stale"|"safety"|"cap"|"cash"|"risk"|"filter"), safety (obj or null), ts.

## Safety (coin check)
{ok(bool), liq, vol24, ageMin, mcap, mintAuth(bool: authority still set), freezeAuth(bool), top10Pct, topPct (largest non-pool holder %), athPct(null = pump.fun unavailable), fails:[str]}

## Strategy
{exitRule:"mirror"\|"risk"\|"hybrid" (what is live), compare:[{rule, desc, put, got, x, pnl, winRate, pf, maxDD, trades}], chosenWhy}

## Additions (v2 final)
- strategy.chosen (rule name, e.g. "hybrid-30/2x/25"), strategy.days, strategy.coins, strategy.delayPen (calibrated entry drift vs leader fill, fraction).
- shortlist2.json carries `hygiene`: leader buys < 0.05 SOL or pump.fun price below bonding-curve start are ignored (scorer, backtest and live copier).

## v3 additions (copy-correctness audit) — additive only
- `positions[].fill` ("jupiter"|"dexscreener"), `vsLeaderPct` (our fill price vs leader's executed price, %), `impactPct` (Jupiter price impact for our ticket), `fillLagS` (leader block time -> our simulated send, s)
- `closes[].fill`
- `detect.detectLagMedS` (leader block -> we parsed his tx), `detect.fillLagMedS`, `detect.vsLeaderMedPct`, `detect.fillSource`, `detect.execDelayS`
- `shortlist[]/scouts[]`: `why` (list of sources that surfaced the wallet), `x7`, `idleH`, `zeroShare`, `entryMcapMed`, `snipeShare`, `avgBuyUsd`, `stTags`, `address`
- `backtest.auditVersion`, `backtest.gates`, `backtest.hygiene`, `backtest.srcCounts`, `backtest.winners[]` {sym, mint, src}
- `cfg.fillSource`, `cfg.prioFeeSol`, `cfg.copyAdds`; `cfg.delay` now = EXEC_DELAY_S (1.5s after detection)

## Hype Sniper (removed Nov 1 2026)
The `hype` key and GET /api/hype are gone; the desk runs the copier only.
