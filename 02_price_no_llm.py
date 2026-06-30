# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# Lab contract 02 — price oracle WITHOUT an LLM.
#
# Hypothesis: a stable public HTTP API (DexScreener search) returns the same
# price across all validators within a tight ±0.1% window when queried close
# in time. If true, the validator can pass with strict numeric tolerance and
# no LLM is needed — the network reaches consensus on deterministically parsed
# JSON. This is the cheapest, fastest oracle shape.
#
# Path: leader fetches search results, picks the best USD-quoted pair for the
# requested symbol+chain, returns price_usd as a string. Validators repeat the
# fetch+parse and compare numerically with abs(leader - mine) / leader < 0.001.
#
# Notes:
# - We deliberately use gl.nondet.web.render() (headless browser variant) per
#   the lab spec for the no-LLM contract, since the goal is to test render()
#   in addition to plain get() (used in 03/04).
# - All gl.nondet.* calls happen inside leader_fn / validator_fn, never at
#   module load or __init__.
# - DexScreener search endpoint: https://api.dexscreener.com/latest/dex/search?q={SYMBOL}
#   Pairs come back with chainId, baseToken.symbol, priceUsd, liquidity.usd.
#   We filter by chain + symbol match and pick the highest-liquidity pair.

from genlayer import *
import json


ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
TOLERANCE = 0.001  # 0.1% — tight numeric tolerance for live DEX prices.


def _within(a: float, b: float, tol: float) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / ((a + b) / 2.0) <= tol


def _http_render_json(url: str) -> dict:
    """Fetch a URL via the headless-browser render path and parse JSON."""
    response = gl.nondet.web.render(url)
    status = getattr(response, "status", 200)
    if 400 <= status < 500:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} returned {status}")
    if status >= 500:
        raise gl.vm.UserError(f"{ERROR_TRANSIENT} {url} returned {status}")
    body = getattr(response, "body", b"")
    try:
        if isinstance(body, bytes):
            text = body.decode("utf-8", errors="ignore")
        else:
            text = str(body)
    except Exception as e:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} unreadable body: {e}")
    try:
        return json.loads(text)
    except Exception as e:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} non-JSON body: {e}")


def _pick_price(payload: dict, symbol: str, chain: str) -> float:
    """Pick the best USD-quoted price for symbol on chain from DexScreener search."""
    pairs = payload.get("pairs") or []
    if not isinstance(pairs, list) or len(pairs) == 0:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} no pairs for {symbol}")

    sym_upper = (symbol or "").upper()
    chain_lower = (chain or "").lower()

    best_price = 0.0
    best_liq = -1.0
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        pair_chain = str(pair.get("chainId") or "").lower()
        if chain_lower and pair_chain != chain_lower:
            continue
        base = pair.get("baseToken") or {}
        base_sym = str(base.get("symbol") or "").upper()
        if sym_upper and base_sym != sym_upper:
            continue
        price_str = pair.get("priceUsd")
        if price_str is None:
            continue
        try:
            price = float(price_str)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        liq_obj = pair.get("liquidity") or {}
        try:
            liq_usd = float(liq_obj.get("usd") or 0)
        except (TypeError, ValueError):
            liq_usd = 0.0
        if liq_usd > best_liq:
            best_liq = liq_usd
            best_price = price

    if best_price <= 0:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} no usable {sym_upper} pair on {chain_lower}")
    return best_price


def _compute_price(symbol: str, chain: str) -> dict:
    url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
    payload = _http_render_json(url)
    price = _pick_price(payload, symbol, chain)
    return {"price_usd": str(price)}


class PriceNoLlm(gl.Contract):
    symbol: str
    chain: str
    price_usd: str
    resolved: bool

    def __init__(self, symbol: str, chain: str):
        self.symbol = symbol
        self.chain = chain
        self.price_usd = "0"
        self.resolved = False

    @gl.public.write
    def resolve(self):
        def leader_fn() -> dict:
            return _compute_price(self.symbol, self.chain)

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                # Mirror the leader's error path so a transient upstream
                # outage doesn't fail the whole tx via validator DISAGREE.
                try:
                    _compute_price(self.symbol, self.chain)
                    return False
                except gl.vm.UserError as e:
                    return str(e) == getattr(leaders_res, "message", "")
            try:
                leader_price = float(leaders_res.calldata["price_usd"])
            except (KeyError, TypeError, ValueError):
                return False
            mine = _compute_price(self.symbol, self.chain)
            try:
                my_price = float(mine["price_usd"])
            except (KeyError, TypeError, ValueError):
                return False
            return _within(leader_price, my_price, TOLERANCE)

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        self.price_usd = result["price_usd"]
        self.resolved = True

    @gl.public.view
    def get_price(self) -> dict:
        return {
            "symbol": self.symbol,
            "chain": self.chain,
            "price_usd": self.price_usd,
            "resolved": self.resolved,
        }
