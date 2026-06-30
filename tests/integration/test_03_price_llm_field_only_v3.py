# Direct-mode tests for 03_price_llm_field_only_v3.py.
#
# Run from experiments/bradbury/:
#   gltest tests/integration/test_03_price_llm_field_only_v3.py -v -s
#
# Why direct mode: gltest's mock_web/mock_llm cheatcodes ONLY exist on
# the `direct_vm` VMContext from gltest.direct.pytest_plugin. The
# previous `vm_context` fixture was fictional — see the rewritten conftest.
#
# Covers (preserved intent from the previous integration suite):
#   - happy path: LLM returns sane integer micro_usd → contract resolves.
#   - LLM_ERROR: LLM returns garbage (missing field) → [LLM_ERROR].
#   - LLM_ERROR: LLM returns a JSON float → [LLM_ERROR] (type guard).
#   - LLM_ERROR: LLM returns a string carrying a decimal point →
#     [LLM_ERROR] (digit-only regex guard).
#   - EXTERNAL: DexScreener 4xx (400, 404) → [EXTERNAL].
#   - TRANSIENT: DexScreener 503 → [TRANSIENT].

from __future__ import annotations

import pytest

from conftest import (
    dexscreener_payload_btc_base,
    llm_response_garbage,
    llm_response_price,
    llm_response_price_float_leak,
    llm_response_price_micro_string_float_leak,
)


CONTRACT_FILENAME = "03_price_llm_field_only_v3.py"
DEXSCREENER_REGEX = r".*api\.dexscreener\.com/.*"
LLM_PRICE_PROMPT_REGEX = r".*extracting a single numeric field.*"

PRICE_SCALE = 1_000_000_000


def _deploy_btc_base(direct_deploy):
    return direct_deploy(CONTRACT_FILENAME, "BTC", "base")


def test_happy_path_llm_extracts_price_matches_deterministic(direct_vm, direct_deploy):
    """LLM returns a sensible integer price → contract resolves with that value."""
    price_str = "65000.123456789"
    payload = dexscreener_payload_btc_base(price_usd=price_str, liquidity_usd="9999999")
    direct_vm.mock_web(
        DEXSCREENER_REGEX,
        {"status": 200, "body": payload},
    )
    direct_vm.mock_llm(
        LLM_PRICE_PROMPT_REGEX,
        llm_response_price(65000.50),
    )

    contract = _deploy_btc_base(direct_deploy)
    contract.resolve()

    state = contract.get_price()
    assert state["resolved"] is True
    assert state["symbol"] == "BTC"
    assert state["chain"] == "base"

    micro = int(state["price_micro_usd"])
    assert micro > 0
    assert PRICE_SCALE * 1_000 < micro < PRICE_SCALE * 1_000_000


def test_llm_error_garbage_output_blocks_consensus(direct_vm, direct_deploy):
    """LLM returns JSON without `price_micro_usd` → [LLM_ERROR]; state unchanged."""
    direct_vm.mock_web(
        DEXSCREENER_REGEX,
        {"status": 200, "body": dexscreener_payload_btc_base()},
    )
    direct_vm.mock_llm(
        LLM_PRICE_PROMPT_REGEX,
        llm_response_garbage(),
    )

    contract = _deploy_btc_base(direct_deploy)

    with direct_vm.expect_revert("[LLM_ERROR]"):
        contract.resolve()

    state = contract.get_price()
    assert state["resolved"] is False
    assert state["price_micro_usd"] == "0"


def test_llm_error_float_leak_blocks_consensus(direct_vm, direct_deploy):
    """LLM returns a JSON float under price_micro_usd → [LLM_ERROR] via type guard."""
    direct_vm.mock_web(
        DEXSCREENER_REGEX,
        {"status": 200, "body": dexscreener_payload_btc_base(
            price_usd="65000.5", liquidity_usd="999")},
    )
    direct_vm.mock_llm(
        LLM_PRICE_PROMPT_REGEX,
        llm_response_price_float_leak(65000.5),
    )

    contract = _deploy_btc_base(direct_deploy)

    with direct_vm.expect_revert("[LLM_ERROR]"):
        contract.resolve()

    state = contract.get_price()
    assert state["resolved"] is False
    assert state["price_micro_usd"] == "0"


def test_llm_error_string_float_leak_blocks_via_regex(direct_vm, direct_deploy):
    """LLM returns a STRING float under price_micro_usd → [LLM_ERROR] via regex guard.

    Distinct from the numeric-float case: this exercises the string branch of
    _pick_price_micro_with_llm — isinstance(val, str) → True, then
    _DIGITS_ONLY_RE.match(s) → None because the string carries a decimal point.
    """
    direct_vm.mock_web(
        DEXSCREENER_REGEX,
        {"status": 200, "body": dexscreener_payload_btc_base(
            price_usd="65000.5", liquidity_usd="999")},
    )
    direct_vm.mock_llm(
        LLM_PRICE_PROMPT_REGEX,
        llm_response_price_micro_string_float_leak(65000.5),
    )

    contract = _deploy_btc_base(direct_deploy)

    with direct_vm.expect_revert("[LLM_ERROR]"):
        contract.resolve()

    state = contract.get_price()
    assert state["resolved"] is False
    assert state["price_micro_usd"] == "0"


@pytest.mark.parametrize("status_code", [400, 404])
def test_external_4xx_dexscreener_surfaces_external_error(direct_vm, direct_deploy, status_code):
    """DexScreener returns 4xx → [EXTERNAL] before LLM is ever consulted."""
    direct_vm.mock_web(
        DEXSCREENER_REGEX,
        {"status": status_code, "body": "Not Found"},
    )
    # LLM mock is a no-op here — leader never reaches it.
    direct_vm.mock_llm(
        LLM_PRICE_PROMPT_REGEX,
        llm_response_price(65000.0),
    )

    contract = _deploy_btc_base(direct_deploy)

    with direct_vm.expect_revert("[EXTERNAL]"):
        contract.resolve()

    state = contract.get_price()
    assert state["resolved"] is False
    assert state["price_micro_usd"] == "0"


def test_transient_503_dexscreener_both_sides_transient(direct_vm, direct_deploy):
    """DexScreener returns 503 → [TRANSIENT] before LLM is ever consulted."""
    direct_vm.mock_web(
        DEXSCREENER_REGEX,
        {"status": 503, "body": "Service Unavailable"},
    )
    direct_vm.mock_llm(
        LLM_PRICE_PROMPT_REGEX,
        llm_response_price(65000.0),
    )

    contract = _deploy_btc_base(direct_deploy)

    with direct_vm.expect_revert("[TRANSIENT]"):
        contract.resolve()

    state = contract.get_price()
    assert state["resolved"] is False
    assert state["price_micro_usd"] == "0"
