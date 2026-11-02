"""Offline tests for the /api/ohlcv cache (no network). Run: python3 test_ohlcv.py"""
import time
import ohlcv

M = "gXCv9BhZCkipp4KLQ7QcVGpTr6AThmJZCLaeKksPapi"
P = "8975unhWt81hKLppmVdke28cgJZzmPXm8etq8XP5GqtP"
calls = []
mode = {"gt": 200}


def fake(url):
    calls.append(url)
    if "dexscreener" in url:
        return 200, [{"chainId": "solana", "pairAddress": P, "liquidity": {"usd": 900}},
                     {"chainId": "solana", "pairAddress": "1" * 40, "liquidity": {"usd": 10}}], {}
    if mode["gt"] == 429:
        return 429, None, {"Retry-After": "20"}
    rows = [[1700000120, 2e-6, 3e-6, 1e-6, 2.5e-6, 10], [1700000060, 1e-6, 2e-6, 1e-6, 2e-6, 5],
            [1700000060, 1e-6, 2e-6, 1e-6, 2e-6, 5], [1700000000, 0, 0, 0, 0, 0]]
    return 200, {"data": {"attributes": {"ohlcv_list": rows}}}, {}


def reset():
    ohlcv._candles.clear(); ohlcv._pools.clear(); calls.clear()
    ohlcv.gt.cool_until = 0; ohlcv.gt.next_at = 0; mode["gt"] = 200


ohlcv._fetch = fake
ohlcv.GT_SPACING = 0

assert ohlcv.candles("not a mint")[0] == 400
assert ohlcv.candles(M, "7m")[0] == 400
assert ohlcv.candles(M, "1m", pool="x/../")[0] == 400

reset()
st, b = ohlcv.candles(M, "1m")
assert st == 200 and b["ok"] and b["pool"] == P, b
assert [c[0] for c in b["candles"]] == [1700000060, 1700000120], "ascending, deduped, zero rows dropped"
assert "token=" + M in calls[-1], "prices quoted in our token"
n = len(calls)
assert ohlcv.candles(M, "1m")[1] is b and len(calls) == n, "served from cache inside TTL"

# expire the cache, then hit a 429: stale copy is served and a cooldown starts
ohlcv._candles[(M, "1m")]["t"] -= 999
mode["gt"] = 429
st, s = ohlcv.candles(M, "1m")
assert st == 200 and s["stale"] and s["err"] == "rate_limited" and s["candles"], s
assert ohlcv.gt.cooling() >= 15
n = len(calls)
ohlcv.candles(M, "1m")
assert len(calls) == n, "no upstream calls while failing / cooling"

# no cached data + cooling -> 503 so the browser backs off
st, s = ohlcv.candles(M, "5m")
assert st == 503 and not s["ok"] and s.get("retryIn"), (st, s)
print("PASS")
