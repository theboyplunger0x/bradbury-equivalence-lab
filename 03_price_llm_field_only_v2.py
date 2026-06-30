# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# Lab contract 03 v2 — price oracle WITH an LLM in the loop.
#
# Codex-driven changes vs v1:
#   1. Already used gl.nondet.web.get() (no change there).
#   2. leader_fn returns a PRIMITIVE STRING (price_micro_usd as decimal text),
#      not a dict. Removes key-order / serialization variance from calldata.
#   3. Integer fixed-point arithmetic: price_micro_usd = int(price * 1e9).
#   4. Advisory fields (raw LLM payload, reasoning, formatting) DO NOT travel
#      in the consensus return — only the primitive price.
#   5. Validator does NOT re-call the LLM. It performs an independent
#      deterministic web.get() of the same JSON endpoint, parses priceUsd
#      itself, and checks that the leader's primitive is within tolerance of
#      its own parsed value. This contains LLM variance to the leader and
#      gives validators a stable, cheap, deterministic check.
#
# Storage is primitives only. @gl.public.view formats them as a dict for
# end-user readability.

from genlayer import *
import json


ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
TOLERANCE = 0.001  # 0.1% — match contract 02 so we can compare consensus.
PRICE_SCALE = 1_000_000_000  # 1e9 — price stored as price_micro_usd integer.


def _within_int(a: int, b: int, tol: float) -> bool:
    if a <= 0 or b <= 0:
        return False
    avg = (a + b) / 2.0
    return abs(a - b) / avg <= tol


def _http_get_text(url: str) -> str:
    response = gl.nondet.web.get(url)
    status = getattr(response, "status", 200)
    if 400 <= status < 500:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} returned {status}")
    if status >= 500:
        raise gl.vm.UserError(f"{ERROR_TRANSIENT} {url} returned {status}")
    body = getattr(response, "body", b"")
    try:
        if isinstance(body, bytes):
            return body.decode("utf-8", errors="ignore")
        return str(body)
    except Exception as e:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} unreadable body: {e}")


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}


def _extract_price_via_llm(symbol: str, chain: str, raw_payload: str) -> float:
    # Cap payload size so the prompt stays reasonable.
    snippet = raw_payload[:12000]
    prompt = f"""
You are extracting a single numeric field from a DexScreener search response.

Symbol requested: {symbol}
Chain requested: {chain}

Task: from the JSON below, pick the pair where chainId == "{chain.lower()}"
AND baseToken.symbol == "{symbol.upper()}" with the HIGHEST liquidity.usd.
Return that pair's priceUsd as a JSON number under the key "price_usd".

Rules:
- Return JSON only.
- If no matching pair exists, return {{"price_usd": 0}}.
- Do NOT include explanations, units, or extra fields.

JSON payload:
{snippet}

Return this exact JSON shape:
{{"price_usd": <number>}}
"""
    raw = gl.nondet.exec_prompt(prompt, response_format="json")
    data = _as_dict(raw)
    val = data.get("price_usd", 0)
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _pick_price_deterministic(payload: dict, symbol: str, chain: str) -> float:
    """Validator-side deterministic parse: no LLM involved.

    Same selection rule as the LLM prompt (highest-liquidity pair matching
    symbol+chain). Used by validator_fn to independently corroborate the
    leader's primitive without re-calling the LLM.
    """
    pairs = payload.get("pairs") or []
    if not isinstance(pairs, list) or len(pairs) == 0:
        return 0.0
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
    return best_price


def _leader_compute_price_micro(symbol: str, chain: str) -> int:
    """Leader path: HTTP fetch + LLM field extraction → integer fixed-point."""
    url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
    raw = _http_get_text(url)
    price = _extract_price_via_llm(symbol, chain, raw)
    if price <= 0:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} no usable {symbol} pair on {chain}")
    return int(price * PRICE_SCALE)


def _validator_compute_price_micro(symbol: str, chain: str) -> int:
    """Validator path: HTTP fetch + deterministic JSON parse. No LLM."""
    url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
    raw = _http_get_text(url)
    try:
        payload = json.loads(raw)
    except Exception as e:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} non-JSON body: {e}")
    if not isinstance(payload, dict):
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} non-object body")
    price = _pick_price_deterministic(payload, symbol, chain)
    if price <= 0:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} no usable {symbol} pair on {chain}")
    return int(price * PRICE_SCALE)


class PriceLlmFieldOnly(gl.Contract):
    symbol: str
    chain: str
    price_micro_usd: str
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
            return str(_leader_compute_price_micro(self.symbol, self.chain))

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                # Mirror error path; do NOT re-call LLM here either.
                try:
                    _validator_compute_price_micro(self.symbol, self.chain)
                    return False
                except gl.vm.UserError as e:
                    return str(e) == getattr(leaders_res, "message", "")
            try:
                leader_micro = int(leaders_res.calldata)
            except (TypeError, ValueError):
                return False
            try:
                my_micro = _validator_compute_price_micro(self.symbol, self.chain)
            except gl.vm.UserError:
                return False
            # Tolerance check on the primitive. LLM variance stays at the
            # leader; validator just sanity-checks the number is within band.
            return _within_int(leader_micro, my_micro, TOLERANCE)

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        self.price_micro_usd = str(result)
        self.resolved = True

    @gl.public.view
    def get_price(self) -> dict:
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
