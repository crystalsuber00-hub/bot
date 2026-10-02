"""Free public data for Solana memecoins: wallet swaps from the public RPC, pools from DexScreener,
recent trades and minute prices from GeckoTerminal. All rate-limited; no keys needed."""
from __future__ import annotations

import logging
import time
from collections import defaultdict

import requests

log = logging.getLogger("memebot")

RPC = "https://api.mainnet-beta.solana.com"
DEX = "https://api.dexscreener.com"
GECKO = "https://api.geckoterminal.com/api/v2/networks/solana"
WSOL = "So11111111111111111111111111111111111111112"
STABLES = {"EPjFWdd5AufqSSqeM2qNPJ8DeZ8bKv6zGcV3MbbU3Q2A",   # USDC
           "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}   # USDT


class Limiter:
    def __init__(self, per_sec: float):
        self.gap, self.last = 1.0 / per_sec, 0.0

    def wait(self):
        d = self.last + self.gap - time.monotonic()
        if d > 0:
            time.sleep(d)
        self.last = time.monotonic()


class Data:
    def __init__(self, rpc: str = RPC, rpc_rate: float = 2.0, gecko_rate: float = 0.45):
        self.s = requests.Session()
        self.rpc_url = rpc
        self.rpc_lim, self.gecko_lim, self.dex_lim = Limiter(rpc_rate), Limiter(gecko_rate), Limiter(4)

    # -- plumbing
    def _get(self, url, lim, **params):
        for attempt in range(5):
            lim.wait()
            try:
                r = self.s.get(url, params=params, timeout=30)
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                return r.json() if r.ok else None
            except (requests.RequestException, ValueError) as e:
                if attempt == 4:
                    log.warning("GET %s failed: %s", url, e)
                    return None
                time.sleep(2 * 2 ** attempt)

    def rpc(self, method, params):
        for attempt in range(6):
            self.rpc_lim.wait()
            try:
                r = self.s.post(self.rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=60)
                d = r.json() if r.ok else None
                if d and "result" in d:
                    return d["result"]
                err = (d or {}).get("error") or {}
                if d and err.get("code") not in (429, -32429):  # a real error (bad request, pruned data): don't retry
                    log.debug("RPC %s error: %s", method, err)
                    return None
                raise requests.HTTPError(f"HTTP {r.status_code} {str(err)[:120]}")
            except (requests.RequestException, ValueError) as e:
                if attempt == 5:
                    log.warning("RPC %s failed: %s", method, e)
                    return None
                time.sleep(1.5 * 2 ** attempt)

    # -- wallets
    def signatures(self, wallet: str, since: float, cap: int) -> list[dict] | None:
        """Successful transaction signatures since `since`, newest first; None if more than `cap` (too busy)."""
        out, before = [], None
        while True:
            opts = {"limit": 1000, **({"before": before} if before else {})}
            page = self.rpc("getSignaturesForAddress", [wallet, opts])
            if page is None:
                return None
            for s in page:
                if (s.get("blockTime") or 0) < since:
                    return [x for x in out if not x.get("err")]
                out.append(s)
                if len(out) > cap:
                    return None
            if len(page) < 1000:
                return [x for x in out if not x.get("err")]
            before = page[-1]["signature"]

    def transactions(self, sigs: list[str]) -> list[dict]:
        out = []
        for sig in sigs:
            tx = self.rpc("getTransaction", [sig, {"maxSupportedTransactionVersion": 1, "encoding": "jsonParsed"}])
            if tx:
                out.append(tx)
        return out

    # -- markets
    def sol_usd(self) -> float:
        d = self._get("https://lite-api.jup.ag/price/v3", self.dex_lim, ids=WSOL) or {}
        return float((d.get(WSOL) or {}).get("usdPrice") or 150.0)

    def boosted_mints(self) -> list[str]:
        mints = []
        for path in ("/token-boosts/top/v1", "/token-boosts/latest/v1", "/token-profiles/latest/v1"):
            for x in self._get(DEX + path, self.dex_lim) or []:
                if x.get("chainId") == "solana" and x.get("tokenAddress") not in mints:
                    mints.append(x["tokenAddress"])
        return mints

    def best_pool(self, mint: str) -> dict | None:
        """Most liquid SOL-quoted pool for a token."""
        pairs = self._get(f"{DEX}/token-pairs/v1/solana/{mint}", self.dex_lim) or []
        pairs = [p for p in pairs if p.get("quoteToken", {}).get("address") == WSOL and p.get("baseToken", {}).get("address") == mint]
        if not pairs:
            return None
        p = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
        return {"pool": p["pairAddress"], "symbol": p["baseToken"].get("symbol", ""), "dex": p.get("dexId"),
                "liquidity": (p.get("liquidity") or {}).get("usd") or 0}

    def pool_traders(self, pool: str) -> list[dict]:
        d = self._get(f"{GECKO}/pools/{pool}/trades", self.gecko_lim) or {}
        return [t["attributes"] for t in d.get("data") or []]

    def candles(self, pool: str, before: int, limit: int = 1000) -> list[list[float]]:
        """Minute candles [t, open, high, low, close, volume] in USD, oldest first, ending before `before`."""
        d = self._get(f"{GECKO}/pools/{pool}/ohlcv/minute", self.gecko_lim, aggregate=1, before_timestamp=before,
                      limit=limit, currency="usd", token="base") or {}
        rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        return sorted({int(r[0]): r for r in rows}.values(), key=lambda r: r[0])


# -- parsing -------------------------------------------------------------------------------

def parse_swap(tx: dict, wallet: str, sol_usd: float = 150.0) -> dict | None:
    """A token swap by `wallet` paid in SOL or in USDC/USDT, or None. `usd` is the dollar amount paid or
    received (SOL legs valued at `sol_usd`); `sol` is the same in SOL.

    SOL spent/received is the wallet's native balance change plus any wrapped-SOL change, so it includes
    the network fee and any token-account rent (refunded when the account is closed on exit).
    """
    meta = tx.get("meta") or {}
    if meta.get("err"):
        return None
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in msg.get("accountKeys") or []]
    la = meta.get("loadedAddresses") or {}
    if keys and isinstance((msg.get("accountKeys") or [None])[0], str):
        keys += la.get("writable", []) + la.get("readonly", [])
    if wallet not in keys:
        return None
    i = keys.index(wallet)
    sol = (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9
    tok = defaultdict(float)
    for sign, bals in ((-1, meta.get("preTokenBalances") or []), (1, meta.get("postTokenBalances") or [])):
        for b in bals:
            if b.get("owner") == wallet:
                tok[b["mint"]] += sign * float(b["uiTokenAmount"].get("uiAmount") or 0)
    sol += tok.pop(WSOL, 0.0)
    stable = sum(tok.pop(m, 0.0) for m in STABLES)
    moved = {m: v for m, v in tok.items() if abs(v) > 1e-12}
    if len(moved) != 1:
        return None
    mint, amt = next(iter(moved.items()))
    quote = stable if abs(stable) > 1e-3 else sol * sol_usd   # paid in dollars, else in SOL
    if amt > 0 and quote < 0:
        side, usd = "buy", -quote
    elif amt < 0 and quote > 0:
        side, usd = "sell", quote
    else:
        return None
    return {"t": tx.get("blockTime"), "mint": mint, "side": side, "tokens": abs(amt), "usd": usd, "sol": usd / sol_usd,
            "sig": ((tx.get("transaction") or {}).get("signatures") or [""])[0]}
