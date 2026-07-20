# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# Lab contract 02 v4 — price oracle WITHOUT an LLM.
#
# A deterministic price oracle whose interesting problem is NOT "read a price".
# It is: how do you reach consensus on a value that legitimately CHANGES between
# the moment the leader reads it and the moment each validator re-reads it?
# Exact-equality consensus is impossible for a live DEX price. This contract
# answers that with a basis-points tolerance band computed in pure integer
# arithmetic, plus a validator that independently re-fetches and re-derives
# rather than rubber-stamping the leader's output.
#
# Changes vs v3 (found by adversarial review after v3's 5/5 AGREE run):
#   1. TERMINAL LATCH BUG. v3 ended resolve() with an unconditional
#      `self.resolved = True` while resolve() opens with a
#      "if self.resolved: raise already resolved" guard. So a run where
#      consensus produced no usable price still latched, permanently bricking
#      the oracle: `price_micro_usd` was pinned to a non-price (e.g. "None")
#      and no later call could ever fix it. v4 latches ONLY on a parseable
#      positive price; a failed run leaves the contract resolvable again.
#   2. STRICT UTF-8 DECODE. v3 used `body.decode("utf-8", errors="ignore")`,
#      which silently drops invalid bytes. Two validators handed different
#      bytes could therefore derive different text and never notice. v4
#      decodes strictly; a decode failure is deterministic for identical bytes
#      and reconciles cleanly as an [EXTERNAL] error.
#   3. Corrected a false comment in the v3 header: it claimed the helpers were
#      IMPORTED from a sibling `_genlayer_helpers` module. They are not, and
#      must not be — see the inlining note below.
#
# Kept from v3: web.get (not render), primitive string return from the leader,
# integer fixed-point storage, 4-class error prefix scheme, 50 bps tolerance,
# advisory formatting confined to the view.

from genlayer import *
import json


# --- Inlined GenLayer helpers -------------------------------------------------
# These are INLINED, not imported. A GenLayer contract runs in a per-validator
# sandbox that cannot see sibling local modules at validator load time, so
# `from _genlayer_helpers import ...` raises ImportError on every validator and
# the deploy returns FINISHED_WITH_ERROR. Single-file is a hard requirement
# unless you explicitly use the py-genlayer-multi runner.

# Canonical error prefix scheme. Each prefix tags a different determinism class
# so a validator knows how to compare its own failure against the leader's.
ERROR_EXPECTED = "[EXPECTED]"   # Business-logic error from the contract itself (deterministic).
ERROR_EXTERNAL = "[EXTERNAL]"   # External API returned a deterministic 4xx (deterministic).
ERROR_TRANSIENT = "[TRANSIENT]" # Network failure or external 5xx (non-deterministic).
ERROR_LLM_ERROR = "[LLM_ERROR]" # LLM misbehavior / unparseable LLM output (non-deterministic).


def _handle_leader_error(leaders_res, leader_fn) -> bool:
    """Leader-error reconciliation.

    Called by validator_fn when the leader did NOT return successfully. The
    validator independently runs `leader_fn()` and decides whether to AGREE or
    DISAGREE with the leader's failure based on its determinism class:

      - EXPECTED / EXTERNAL (deterministic): agree only on a BYTE-EQUAL message.
      - TRANSIENT (non-deterministic):       agree if BOTH sides hit a transient.
      - LLM_ERROR / unknown:                 ALWAYS disagree, forcing leader
                                             rotation and a consensus retry.

    Returns True to AGREE with the leader's failure, False to DISAGREE.

    Without this, a single transient 503 on the leader's side produces a
    spurious DISAGREE and burns a consensus round even though nothing is wrong.
    """
    leader_msg = getattr(leaders_res, "message", "") or ""
    try:
        leader_fn()
        # The validator succeeded where the leader failed: the leader is the
        # outlier, so disagree.
        return False
    except gl.vm.UserError as e:
        validator_msg = getattr(e, "message", None)
        if validator_msg is None:
            validator_msg = str(e)
        # Deterministic classes: identical inputs must produce an identical
        # message, so require a byte-exact match.
        if validator_msg.startswith(ERROR_EXPECTED) or validator_msg.startswith(ERROR_EXTERNAL):
            return validator_msg == leader_msg
        # Transient: agree if both sides independently saw a transient failure.
        if validator_msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
            return True
        # LLM_ERROR or anything unrecognized: disagree to force a retry.
        return False
    except Exception:
        # Deliberate catch-all, and deliberately fail-SAFE: an unclassified
        # runtime exception is never a clean determinism class, so we disagree
        # and let consensus retry with a different leader. This is the one place
        # a broad except is correct, because it converts an unknown into a
        # DISAGREE rather than masking it into a false AGREE.
        return False


def _within_int(a: int, b: int, tol_bps: int) -> bool:
    """Basis-points tolerance compare for integer fixed-point prices.

    Integer arithmetic only. A live DEX price moves between the leader's fetch
    and each validator's fetch, so requiring exact equality would make consensus
    impossible; requiring "close enough" in float math would reintroduce
    platform-dependent rounding. This does it in integers:

        diff / ((a + b) / 2) <= tol_bps / 10_000
      becomes
        diff * 20_000 <= (a + b) * tol_bps

    tol_bps is in basis points (1 bp = 0.01%), so tol_bps=50 means 0.5%.
    """
    if a <= 0 or b <= 0:
        return False
    diff = a - b if a >= b else b - a
    return diff * 20_000 <= (a + b) * tol_bps


# 50 bps == 0.5%, wide enough to absorb intra-block DEX movement between the
# leader's fetch and a validator's fetch seconds later, tight enough that a
# genuinely different pair or a manipulated quote still fails the band.
TOLERANCE_BPS = 50
PRICE_SCALE = 1_000_000_000  # 1e9 — price stored as an integer price_micro_usd.


def _http_get_json(url: str) -> dict:
    """Fetch a URL via plain web.get and parse JSON, classifying failures."""
    response = gl.nondet.web.get(url)
    status = getattr(response, "status", 200)
    if 400 <= status < 500:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} returned {status}")
    if status >= 500:
        raise gl.vm.UserError(f"{ERROR_TRANSIENT} {url} returned {status}")
    body = getattr(response, "body", b"")
    try:
        if isinstance(body, bytes):
            # v4: STRICT decode. errors="ignore" silently discards invalid
            # bytes, so two validators handed different bytes could derive
            # different text and agree on a value neither actually read. A
            # decode failure is deterministic for identical bytes, so it
            # reconciles as an [EXTERNAL] error below.
            text = body.decode("utf-8")
        else:
            text = str(body)
    except (UnicodeError, ValueError) as e:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} unreadable body: {e}")
    try:
        return json.loads(text)
    except (ValueError, TypeError) as e:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} non-JSON body: {e}")


def _parse_decimal_to_micro(value) -> int:
    """Parse a decimal string or number into an integer scaled by PRICE_SCALE.

    Pure integer math, no float casting anywhere: a float would make the value
    platform-rounding dependent and therefore consensus-hostile. Accepts
    "1.234", "0.000123", "42", ints. Returns 0 on parse failure.
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
    # Truncate or pad the fractional part to PRICE_SCALE's digit count.
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


def _pick_price_micro(payload: dict, symbol: str, chain: str) -> int:
    """Pick the USD price for symbol+chain and return it as integer micro_usd.

    Selection rule, which must be DETERMINISTIC so every validator picks the
    same pair from the same payload: among pairs matching chainId and
    baseToken.symbol, take the one with the highest liquidity.usd.
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


def _compute_price_micro(symbol: str, chain: str) -> int:
    """Return the integer fixed-point price_micro_usd = price * 1e9."""
    url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
    payload = _http_get_json(url)
    return _pick_price_micro(payload, symbol, chain)


class PriceNoLlm(gl.Contract):
    symbol: str
    chain: str
    price_micro_usd: str  # integer stored as a decimal string, "0" until resolved
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
            # PRIMITIVE return: an integer as a decimal string, never a dict.
            # Dict/JSON returns reintroduce key-order and serialization variance
            # into the consensus hash.
            return str(_compute_price_micro(symbol, chain))

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return _handle_leader_error(leaders_res, leader_fn)
            try:
                leader_micro = int(leaders_res.calldata)
            except (TypeError, ValueError):
                return False
            try:
                # The validator does the SAME work the leader did: re-fetch,
                # re-select the pair, re-derive the price. It never trusts the
                # leader's value, it only compares against its own.
                my_micro = _compute_price_micro(symbol, chain)
            except gl.vm.UserError:
                # Validator cannot fetch but the leader could: disagree.
                return False
            return _within_int(leader_micro, my_micro, TOLERANCE_BPS)

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        # v4: latch ONLY on a usable price. If consensus produced nothing
        # parseable and positive, leave the contract UNRESOLVED so it can be
        # resolved again once the source recovers. v3 latched unconditionally,
        # which pinned a non-price forever after a single transient failure.
        price_text = str(result)
        try:
            price_micro = int(price_text)
        except (TypeError, ValueError):
            price_micro = 0
        if price_micro > 0:
            self.price_micro_usd = price_text
            self.resolved = True

    @gl.public.view
    def get_price(self) -> dict:
        # Storage stays primitive; the view is free to format for readability
        # because a view never participates in consensus.
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
