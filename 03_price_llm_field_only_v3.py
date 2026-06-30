# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# Lab contract 03 v3 — price oracle WITH an LLM in the loop.
#
# What this contract actually validates:
#   The LEADER calls an LLM to extract priceUsd from a DexScreener JSON
#   response. The VALIDATOR independently re-fetches the same JSON endpoint
#   and re-derives the price DETERMINISTICALLY (no LLM), then tolerance-
#   compares against the leader's primitive. LLM variance is contained to
#   the leader; validators stay cheap, deterministic, and reproducible.
#
# Changes vs v2 (skills.genlayer.com SKILL.md anti-pattern fixes + Codex):
#   1. Imports canonical _handle_leader_error + _within_int from
#      _genlayer_helpers — drops the ad-hoc `except gl.vm.UserError` branch
#      and adopts the 4-class error scheme (EXPECTED / EXTERNAL / TRANSIENT
#      / LLM_ERROR).
#   2. Tolerance widened 0.001 → 0.005 (50 bps == 0.5%) to absorb DEX
#      intra-block movement between leader and validator fetches.
#   3. LLM parse failure now raises [LLM_ERROR] instead of returning 0.0 ─
#      the v2 silent-zero path was the "Ignore LLM response format"
#      anti-pattern. The validator-side deterministic path still works
#      independently (it doesn't see the LLM), and _handle_leader_error
#      DISAGREES on LLM_ERROR to force consensus retry.
#   4. All arithmetic is integer / decimal-string parsing — no bare float
#      in tolerance math or storage (lint AST bare-float check passes).
#   5. Already kept from v2: web.get (not render), primitive string return,
#      validator does NOT re-call the LLM.

from genlayer import *
import json
import re


# --- Inlined GenLayer helpers (canonical anti-pattern fixes from SKILL.md) ----
# Inlined because GenLayer contracts run in a per-validator sandbox that does
# NOT have access to sibling local modules at validator load time. A
# importing from a sibling helpers module raises ImportError on every validator and
# the deploy comes back FINISHED_WITH_ERROR. Keep this block in lock-step with
# the source-of-truth helpers file (experiments/bradbury directory).

# Canonical error prefix scheme (per SKILL.md errorPrefixScheme). Each prefix
# tags a different deterministic / non-deterministic class so validators know
# how to compare their own error against the leader's.
ERROR_EXPECTED = "[EXPECTED]"   # Business-logic error from the contract itself (deterministic).
ERROR_EXTERNAL = "[EXTERNAL]"   # External API returned a deterministic 4xx (deterministic).
ERROR_TRANSIENT = "[TRANSIENT]" # Network failure or external 5xx (non-deterministic).
ERROR_LLM_ERROR = "[LLM_ERROR]" # LLM misbehavior / unparseable LLM output (non-deterministic).


def _handle_leader_error(leaders_res, leader_fn) -> bool:
    """Canonical leader-error reconciliation per SKILL.md.

    Called by validator_fn when the leader did NOT return successfully
    (i.e. leaders_res is not gl.vm.Return). The validator independently
    runs `leader_fn()` and decides whether to AGREE or DISAGREE with the
    leader's error based on the prefix class:

      - EXPECTED  / EXTERNAL  (deterministic): agree only on BYTE-EQUAL message.
      - TRANSIENT (non-deterministic):         agree if BOTH hit any TRANSIENT.
      - LLM_ERROR / unknown:                   ALWAYS disagree — forces leader
                                               rotation and a consensus retry.

    Returns True to AGREE with the leader's failure, False to DISAGREE.
    """
    leader_msg = getattr(leaders_res, "message", "") or ""
    try:
        leader_fn()
        # Validator ran successfully but the leader failed → disagree, the
        # leader is the outlier.
        return False
    except gl.vm.UserError as e:
        validator_msg = getattr(e, "message", None)
        if validator_msg is None:
            validator_msg = str(e)
        # Deterministic classes: byte-exact match required.
        if validator_msg.startswith(ERROR_EXPECTED) or validator_msg.startswith(ERROR_EXTERNAL):
            return validator_msg == leader_msg
        # Transient: agree if both sides independently saw a transient failure.
        if validator_msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
            return True
        # LLM_ERROR or anything unrecognized: disagree to force retry.
        return False
    except Exception:
        # A non-UserError (raw runtime exception) is never a clean class —
        # disagree so consensus retries with a different leader.
        return False


def _within_int(a: int, b: int, tol_bps: int) -> bool:
    """Basis-points tolerance compare for integer fixed-point prices.

    Uses INTEGER arithmetic only — no bare float — so the lint AST
    'Non-deterministic patterns (bare float usage)' anti-pattern check
    passes cleanly.

    tol_bps is in basis points (1 bp = 0.01%). e.g. tol_bps=50 == 0.5%.
    """
    if a <= 0 or b <= 0:
        return False
    diff = a - b if a >= b else b - a
    # Compare diff * 20_000 against (a + b) * tol_bps — equivalent to
    #   diff / ((a + b) / 2) <= tol_bps / 10_000
    # but using only integers.
    return diff * 20_000 <= (a + b) * tol_bps


# LLM is constrained to return ONLY digits (price encoded as price * PRICE_SCALE
# in integer micro-USD). Anything containing a decimal point, currency symbol,
# scientific notation, sign, or whitespace inside the digits is rejected as
# LLM_ERROR — preventing float-string leaks that would non-deterministically
# truncate or raise on int() cast.
_DIGITS_ONLY_RE = re.compile(r"^\d+$")


TOLERANCE_BPS = 50  # 0.5% — matches contract 02 v3 so consensus is comparable.
PRICE_SCALE = 1_000_000_000  # 1e9 — price stored as price_micro_usd integer.


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
    except (UnicodeError, ValueError) as e:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} unreadable body: {e}")


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            return {}
    return {}


def _parse_decimal_to_micro(value) -> int:
    """Pure-integer decimal-string parser to micro_usd. Returns 0 on failure."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value * PRICE_SCALE
    s = str(value).strip()
    if not s:
        return 0
    neg = False
    if s.startswith("-"):
        neg = True
        s = s[1:]
    elif s.startswith("+"):
        s = s[1:]
    if "." in s:
        whole, frac = s.split(".", 1)
    else:
        whole, frac = s, ""
    scale_digits = len(str(PRICE_SCALE)) - 1
    if len(frac) > scale_digits:
        frac = frac[:scale_digits]
    else:
        frac = frac + "0" * (scale_digits - len(frac))
    if whole and not whole.isdigit():
        return 0
    if frac and not frac.isdigit():
        return 0
    try:
        whole_int = int(whole) if whole else 0
        frac_int = int(frac) if frac else 0
    except ValueError:
        return 0
    micro = whole_int * PRICE_SCALE + frac_int
    return -micro if neg else micro


def _extract_price_micro_via_llm(symbol: str, chain: str, raw_payload: str) -> int:
    """Leader-only path: prompt the LLM to extract priceUsd.

    The LLM is constrained to return an INTEGER ONLY (price * PRICE_SCALE,
    encoded as price_micro_usd). Any float-string ("1234.56"), currency
    symbol, scientific notation, or non-digit character is rejected as
    LLM_ERROR — preventing nondeterministic int()-cast behavior (which
    would either raise ValueError or silently truncate depending on the
    Python build's str-to-int coercion path).

    Returns the integer micro_usd. Raises ERROR_LLM_ERROR if the LLM
    output is unparseable, missing the expected key, or not strictly
    digit-only — per SKILL.md, this forces validators to disagree and
    triggers consensus retry.
    """
    snippet = raw_payload[:12000]
    prompt = f"""
You are extracting a single numeric field from a DexScreener search response.

Symbol requested: {symbol}
Chain requested: {chain}

Task: from the JSON below, pick the pair where chainId == "{chain.lower()}"
AND baseToken.symbol == "{symbol.upper()}" with the HIGHEST liquidity.usd.
Take that pair's priceUsd field and convert it to MICRO-USD by multiplying
by 1_000_000_000 (one billion), then ROUNDING to the nearest integer.
Return that integer under the key "price_micro_usd".

CRITICAL formatting rules (any violation = invalid response):
- "price_micro_usd" MUST be a JSON integer (no decimal point, no fraction).
- Do NOT return a float, a string, scientific notation, or units.
- Do NOT include currency symbols ($, USD), commas, signs, or whitespace
  inside the number.
- Allowed values are non-negative integers only (0 or positive).
- If no matching pair exists, return {{"price_micro_usd": 0}}.
- Return JSON only — no explanations, no extra keys.

Example for priceUsd = "65000.123456789":
{{"price_micro_usd": 65000123456789}}

JSON payload:
{snippet}

Return this exact JSON shape:
{{"price_micro_usd": <integer>}}
"""
    raw = gl.nondet.exec_prompt(prompt, response_format="json")
    data = _as_dict(raw)
    if "price_micro_usd" not in data:
        raise gl.vm.UserError(f"{ERROR_LLM_ERROR} missing price_micro_usd in LLM output")
    val = data.get("price_micro_usd", 0)

    # Strict digit-only validation BEFORE int() cast. We accept either a
    # JSON integer (Python int, but NOT bool — bool is an int subclass) or
    # a digit-only string. Floats, scientific notation, signed values,
    # whitespace, and currency-prefixed strings are all rejected.
    if isinstance(val, bool):
        raise gl.vm.UserError(f"{ERROR_LLM_ERROR} non-integer price_micro_usd: bool")
    if isinstance(val, int):
        micro = val
    elif isinstance(val, str):
        s = val.strip()
        if not _DIGITS_ONLY_RE.match(s):
            raise gl.vm.UserError(
                f"{ERROR_LLM_ERROR} non-digit price_micro_usd string: {s[:32]!r}"
            )
        try:
            micro = int(s)
        except ValueError:
            raise gl.vm.UserError(
                f"{ERROR_LLM_ERROR} unparseable price_micro_usd string: {s[:32]!r}"
            )
    else:
        raise gl.vm.UserError(
            f"{ERROR_LLM_ERROR} non-numeric price_micro_usd: {type(val).__name__}"
        )

    if micro < 0:
        raise gl.vm.UserError(f"{ERROR_LLM_ERROR} negative price_micro_usd")
    if micro == 0:
        # Treat zero as "no pair found" per the prompt contract — that is a
        # deterministic external condition, NOT an LLM bug. Surface it as
        # EXTERNAL so deterministic validators reach the same conclusion
        # from the raw JSON.
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} no usable {symbol} pair on {chain}")
    return micro


def _pick_price_micro_deterministic(payload: dict, symbol: str, chain: str) -> int:
    """Validator-side deterministic parse: no LLM. Pure integer arithmetic."""
    pairs = payload.get("pairs") or []
    if not isinstance(pairs, list) or len(pairs) == 0:
        return 0
    sym_upper = (symbol or "").upper()
    chain_lower = (chain or "").lower()
    best_micro = 0
    best_liq_micro = -1
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
        price_micro = _parse_decimal_to_micro(pair.get("priceUsd"))
        if price_micro <= 0:
            continue
        liq_obj = pair.get("liquidity") or {}
        liq_micro = _parse_decimal_to_micro(liq_obj.get("usd"))
        if liq_micro > best_liq_micro:
            best_liq_micro = liq_micro
            best_micro = price_micro
    return best_micro


def _leader_compute_price_micro(symbol: str, chain: str) -> int:
    """Leader path: HTTP fetch + LLM field extraction → integer micro_usd."""
    url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
    raw = _http_get_text(url)
    return _extract_price_micro_via_llm(symbol, chain, raw)


def _validator_compute_price_micro(symbol: str, chain: str) -> int:
    """Validator path: HTTP fetch + deterministic JSON parse. No LLM."""
    url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
    raw = _http_get_text(url)
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError) as e:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} non-JSON body: {e}")
    if not isinstance(payload, dict):
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} non-object body")
    micro = _pick_price_micro_deterministic(payload, symbol, chain)
    if micro <= 0:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} no usable {symbol} pair on {chain}")
    return micro


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
        if self.resolved:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} already resolved")

        symbol = self.symbol
        chain = self.chain

        def leader_fn() -> str:
            # PRIMITIVE return: integer as decimal string. No dict.
            return str(_leader_compute_price_micro(symbol, chain))

        # Validator-side proxy for the canonical error handler. We want the
        # SKILL.md helper to compare the validator's deterministic path
        # against the leader's error class — NOT to re-run the LLM. So we
        # feed _handle_leader_error a validator-shaped leader_fn surrogate.
        def validator_reproduce_fn() -> str:
            return str(_validator_compute_price_micro(symbol, chain))

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                # LLM_ERROR or any unknown → canonical helper returns False
                # (disagree, force retry). EXTERNAL/TRANSIENT compared per
                # SKILL.md rules using validator's deterministic path.
                return _handle_leader_error(leaders_res, validator_reproduce_fn)
            try:
                leader_micro = int(leaders_res.calldata)
            except (TypeError, ValueError):
                return False
            try:
                my_micro = _validator_compute_price_micro(symbol, chain)
            except gl.vm.UserError:
                return False
            return _within_int(leader_micro, my_micro, TOLERANCE_BPS)

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        self.price_micro_usd = str(result)
        self.resolved = True

    @gl.public.view
    def get_price(self) -> dict:
        try:
            micro = int(self.price_micro_usd)
        except (TypeError, ValueError):
            micro = 0
        whole = micro // PRICE_SCALE if micro > 0 else 0
        frac = micro % PRICE_SCALE if micro > 0 else 0
        scale_digits = len(str(PRICE_SCALE)) - 1
        frac_str = str(frac).rjust(scale_digits, "0").rstrip("0") or "0"
        price_usd_str = f"{whole}.{frac_str}" if micro > 0 else "0"
        return {
            "symbol": self.symbol,
            "chain": self.chain,
            "price_micro_usd": self.price_micro_usd,
            "price_usd": price_usd_str,
            "resolved": self.resolved,
        }
