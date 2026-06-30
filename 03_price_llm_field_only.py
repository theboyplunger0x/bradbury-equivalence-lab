# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# Lab contract 03 — price oracle WITH an LLM in the loop.
#
# Hypothesis: even when an LLM is in the path (formatting varies, ordering
# varies, reason text varies), validators can still reach consensus IF the
# validator extracts ONLY one structured field (`price_usd`) and compares it
# numerically with ±0.1% tolerance. The non-numeric fields (formatting,
# reasoning) are ignored on purpose so LLM variance doesn't break consensus.
#
# Path: leader does the HTTP fetch, then passes the raw JSON to the LLM and
# asks it to extract `price_usd` for symbol on chain. Validators repeat the
# full leader path (fetch + LLM extract) and compare just the numeric field.
#
# Notes:
# - HTTP fetch uses gl.nondet.web.get() (cheaper than render() for a JSON
#   endpoint, and 03 isn't testing render — that's 02's job).
# - LLM call uses gl.nondet.exec_prompt(prompt, response_format="json") which
#   the worldcup oracle uses successfully in production.
# - All gl.nondet.* calls happen inside leader_fn / validator_fn.

from genlayer import *
import json


ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
TOLERANCE = 0.001  # 0.1% — match contract 02 so we can compare consensus.


def _within(a: float, b: float, tol: float) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / ((a + b) / 2.0) <= tol


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


def _compute_price(symbol: str, chain: str) -> dict:
    url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
    raw = _http_get_text(url)
    price = _extract_price_via_llm(symbol, chain, raw)
    if price <= 0:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} no usable {symbol} pair on {chain}")
    return {"price_usd": str(price)}


class PriceLlmFieldOnly(gl.Contract):
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
            # We intentionally compare ONLY price_usd. Any other LLM output
            # variance (formatting, reasoning, ordering) is ignored.
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
