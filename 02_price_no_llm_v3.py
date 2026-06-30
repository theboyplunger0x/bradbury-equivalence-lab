# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# Lab contract 02 v3 — price oracle WITHOUT an LLM.
#
# Changes vs v2 (skills.genlayer.com SKILL.md anti-pattern fixes + Codex):
#   1. Imports canonical _handle_leader_error + _within_int from
#      _genlayer_helpers — drops the ad-hoc `except gl.vm.UserError` branch
#      that did NOT cover the TRANSIENT-both-sides agree case.
#   2. All 4 error prefix classes wired in: EXPECTED / EXTERNAL / TRANSIENT
#      / LLM_ERROR (only EXTERNAL + TRANSIENT used here; EXPECTED reserved
#      for the "already resolved" guard).
#   3. Tolerance widened 0.001 → 0.005 (50 bps == 0.5%) to absorb DEX
#      intra-block movement between leader and validators fetching the
#      same pair seconds apart.
#   4. _within_int now uses pure integer basis-points arithmetic — no bare
#      float in tolerance math (was the last bare-float lint hit in v2).
#   5. Already kept from v2: web.get (not render), primitive string return,
#      integer fixed-point storage, advisory fields outside consensus.

from genlayer import *
import json


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


# 50 bps == 0.5%. Wider than v2's 0.1% to absorb intra-block DEX movement
# between leader fetch and validator fetch.
TOLERANCE_BPS = 50
PRICE_SCALE = 1_000_000_000  # 1e9 — price stored as price_micro_usd integer.


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
    except (UnicodeError, ValueError) as e:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} unreadable body: {e}")
    try:
        return json.loads(text)
    except (ValueError, TypeError) as e:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} non-JSON body: {e}")


def _pick_price_micro(payload: dict, symbol: str, chain: str) -> int:
    """Pick best USD-quoted price for symbol+chain, return integer micro_usd.

    Selection rule: among pairs matching chainId+baseToken.symbol, pick the
    one with the highest liquidity.usd.

    All arithmetic is integer; we parse priceUsd as a decimal string and
    multiply by PRICE_SCALE via integer math so we never store or compare
    floats.
    """
    pairs = payload.get("pairs") or []
    if not isinstance(pairs, list) or len(pairs) == 0:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} no pairs for {symbol}")

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

    if best_micro <= 0:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} no usable {sym_upper} pair on {chain_lower}")
    return best_micro


def _parse_decimal_to_micro(value) -> int:
    """Parse a decimal-string-or-number into an integer * PRICE_SCALE.

    Pure integer math — no float casting. Accepts "1.234", "0.000123",
    "42", ints. Returns 0 on parse failure. This keeps the lint AST
    bare-float check clean.
    """
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
    # Truncate or pad fractional part to match PRICE_SCALE's decimal digits.
    scale_digits = len(str(PRICE_SCALE)) - 1  # 9 for 1e9
    if len(frac) > scale_digits:
        frac = frac[:scale_digits]
    else:
        frac = frac + "0" * (scale_digits - len(frac))
    if not whole.isdigit() and whole != "":
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


def _compute_price_micro(symbol: str, chain: str) -> int:
    """Return integer fixed-point price_micro_usd = price * 1e9."""
    url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
    payload = _http_get_json(url)
    return _pick_price_micro(payload, symbol, chain)


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
        if self.resolved:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} already resolved")

        symbol = self.symbol
        chain = self.chain

        def leader_fn() -> str:
            # PRIMITIVE return: integer as decimal string. No dict.
            return str(_compute_price_micro(symbol, chain))

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                # Canonical SKILL.md leader-error reconciliation: handles
                # deterministic byte-equal, transient-both-sides agree, and
                # LLM/unknown disagree-to-retry.
                return _handle_leader_error(leaders_res, leader_fn)
            try:
                leader_micro = int(leaders_res.calldata)
            except (TypeError, ValueError):
                return False
            try:
                my_micro = _compute_price_micro(symbol, chain)
            except gl.vm.UserError:
                # Validator can't fetch but leader did — disagree.
                return False
            return _within_int(leader_micro, my_micro, TOLERANCE_BPS)

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
        # Format price as decimal string via integer division — no float math.
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
