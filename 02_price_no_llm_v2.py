# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# Lab contract 02 v2 — price oracle WITHOUT an LLM.
#
# Codex-driven changes vs v1:
#   1. gl.nondet.web.get() instead of gl.nondet.web.render() — render() adds
#      DOM/timing variance for what is just a JSON endpoint.
#   2. leader_fn returns a PRIMITIVE STRING (the integer price_micro_usd as
#      decimal text), not a dict. Dicts have key-order / serialization
#      variance in calldata and can break byte-level consensus.
#   3. Integer fixed-point arithmetic: price_micro_usd = int(price * 1e9).
#      Storage holds the integer as a string ("0" sentinel). Floats only live
#      inside the tolerance helper, never in storage and never in calldata.
#   4. Advisory fields (sources, liquidity, etc.) DO NOT travel in the
#      consensus return. Only the primitive validators actually validate.
#   5. validator_fn compares the leader's primitive against its own
#      independent fetch using a tolerance check on the integer — it never
#      re-fetches into a dict and diffs dicts.
#
# Storage is primitives only. @gl.public.view formats them as a dict for
# end-user readability.

from genlayer import *
import json


ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
TOLERANCE = 0.001  # 0.1% — tight numeric tolerance for live DEX prices.
PRICE_SCALE = 1_000_000_000  # 1e9 — price stored as price_micro_usd integer.


def _within_int(a: int, b: int, tol: float) -> bool:
    if a <= 0 or b <= 0:
        return False
    # Compare integers via float ratio of the midpoint; tolerance is small.
    avg = (a + b) / 2.0
    return abs(a - b) / avg <= tol


def _http_get_json(url: str) -> dict:
    """Fetch a URL via plain web.get and parse JSON."""
    response = gl.nondet.web.get(url)
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


def _compute_price_micro(symbol: str, chain: str) -> int:
    """Return integer fixed-point price_micro_usd = int(price * 1e9)."""
    url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
    payload = _http_get_json(url)
    price = _pick_price(payload, symbol, chain)
    return int(price * PRICE_SCALE)


class PriceNoLlm(gl.Contract):
    symbol: str
    chain: str
    price_micro_usd: str  # integer stored as decimal string, "0" until resolved
    resolved: bool

    def __init__(self, symbol: str, chain: str):
        self.symbol = symbol
        self.chain = chain
        self.price_micro_usd = "0"
        self.resolved = False

    @gl.public.write
    def resolve(self):
        def leader_fn() -> str:
            # PRIMITIVE return: integer as decimal string. No dict.
            return str(_compute_price_micro(self.symbol, self.chain))

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                # Mirror the leader's error path so a transient upstream
                # outage doesn't fail the whole tx via validator DISAGREE.
                try:
                    _compute_price_micro(self.symbol, self.chain)
                    return False
                except gl.vm.UserError as e:
                    return str(e) == getattr(leaders_res, "message", "")
            try:
                leader_micro = int(leaders_res.calldata)
            except (TypeError, ValueError):
                return False
            try:
                my_micro = _compute_price_micro(self.symbol, self.chain)
            except gl.vm.UserError:
                return False
            return _within_int(leader_micro, my_micro, TOLERANCE)

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        # `result` is the primitive string the leader returned.
        self.price_micro_usd = str(result)
        self.resolved = True

    @gl.public.view
    def get_price(self) -> dict:
        # Storage stays primitive; the view formats for end-user readability.
        try:
            micro = int(self.price_micro_usd)
        except (TypeError, ValueError):
            micro = 0
        price_usd = micro / PRICE_SCALE if micro > 0 else 0.0
        return {
            "symbol": self.symbol,
            "chain": self.chain,
            "price_micro_usd": self.price_micro_usd,
            "price_usd": str(price_usd),
            "resolved": self.resolved,
        }
