# Shared GenLayer helpers — canonical anti-pattern fixes from skills.genlayer.com
# SKILL.md.
#
# Provides:
#   - 4 canonical error prefix constants (EXPECTED / EXTERNAL / TRANSIENT / LLM_ERROR)
#   - _handle_leader_error: canonical leader-error reconciliation per SKILL.md
#   - _within_int: basis-points tolerance compare for integer fixed-point prices
#     (NO bare float arithmetic — the lint AST anti-pattern check would flag it)
#
# All three v3 contracts (02 price-no-llm, 03 price-llm-field, 04 worldcup-enum)
# import from this module so the error scheme is uniform and consensus is
# byte-stable.

from genlayer import *


# --- Canonical error prefix scheme (per SKILL.md errorPrefixScheme) -----------
# Each prefix tags a different deterministic / non-deterministic class so that
# validators know how to compare their own error against the leader's.
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
