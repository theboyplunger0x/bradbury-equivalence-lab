# Integration tests for 03_price_llm_field_only_v3.py
#
# Run from experiments/bradbury/:
#   gltest tests/integration/test_03_price_llm_field_only_v3.py -v -s --network localnet
#
# Covers:
#   - happy path: LLM returns sane price_usd; validator's deterministic
#     re-parse of the same JSON payload tolerance-matches the leader.
#   - LLM_ERROR: LLM returns garbage (missing price_usd) → leader raises
#     [LLM_ERROR]; canonical _handle_leader_error DISAGREES forcing retry.
#   - transient: DexScreener returns 503 → both leader and validator see
#     a TRANSIENT class and agree per the canonical rule.

from __future__ import annotations

import json

import pytest

from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded

from conftest import (
    dexscreener_payload_btc_base,
    llm_response_garbage,
    llm_response_price,
    llm_response_price_float_leak,
)


CONTRACT_FILENAME = "03_price_llm_field_only_v3.py"
DEXSCREENER_REGEX = r".*api\.dexscreener\.com/.*"
LLM_PRICE_PROMPT_REGEX = r".*extracting a single numeric field.*"

PRICE_SCALE = 1_000_000_000


def _deploy_btc_base(factory):
    return factory.deploy(args=["BTC", "base"])


@pytest.mark.requires_mocks
def test_happy_path_llm_extracts_price_matches_deterministic(vm_context):
    """Leader's LLM extraction is tolerance-equal to validator's deterministic parse."""
    factory = get_contract_factory("PriceLlmFieldOnly", source_file=CONTRACT_FILENAME)

    price_str = "65000.123456789"
    payload = dexscreener_payload_btc_base(price_usd=price_str, liquidity_usd="9999999")
    vm_context.mock_web(
        DEXSCREENER_REGEX,
        {"status": 200, "body": payload},
    )

    # Leader-only LLM call returns the price the JSON also carries — within
    # TOLERANCE_BPS=50 (0.5%) of the validator's deterministic re-derivation.
    vm_context.mock_llm(
        LLM_PRICE_PROMPT_REGEX,
        llm_response_price(65000.50),
    )

    contract = _deploy_btc_base(factory)

    tx_receipt = contract.resolve(args=[]).transact()
    assert tx_execution_succeeded(tx_receipt), (
        "happy path: LLM extraction within tolerance must reach consensus"
    )

    state = contract.get_price(args=[]).call()
    assert state["resolved"] is True
    assert state["symbol"] == "BTC"
    assert state["chain"] == "base"

    micro = int(state["price_micro_usd"])
    assert micro > 0
    assert PRICE_SCALE * 1_000 < micro < PRICE_SCALE * 1_000_000


@pytest.mark.requires_mocks
def test_llm_error_garbage_output_blocks_consensus(vm_context):
    """LLM returns malformed JSON → leader raises [LLM_ERROR]; consensus fails.

    Per SKILL.md canonical scheme, the validator's _handle_leader_error
    sees an LLM_ERROR prefix and DISAGREES — forcing leader rotation. After
    rotation runs out, the tx is reported as failed execution and no state
    changes.
    """
    factory = get_contract_factory("PriceLlmFieldOnly", source_file=CONTRACT_FILENAME)

    payload = dexscreener_payload_btc_base()
    vm_context.mock_web(
        DEXSCREENER_REGEX,
        {"status": 200, "body": payload},
    )
    # Garbage shape — `price_usd` missing entirely.
    vm_context.mock_llm(
        LLM_PRICE_PROMPT_REGEX,
        llm_response_garbage(),
    )

    contract = _deploy_btc_base(factory)

    tx_receipt = contract.resolve(args=[]).transact()
    assert not tx_execution_succeeded(tx_receipt), (
        "LLM_ERROR: garbage LLM output must NOT settle as successful execution"
    )

    state = contract.get_price(args=[]).call()
    assert state["resolved"] is False
    assert state["price_micro_usd"] == "0"


@pytest.mark.requires_mocks
def test_llm_error_float_leak_blocks_consensus(vm_context):
    """LLM returns a JSON float under price_micro_usd → leader raises [LLM_ERROR].

    The v3 prompt requires INTEGER-ONLY output. If the LLM ignores that
    contract and returns a float (e.g. 65000.5 instead of 65000500000000),
    the digit-only regex guard MUST reject before the int() cast — which
    would otherwise truncate or raise nondeterministically. Per the
    canonical scheme, validators disagree and consensus retries.
    """
    factory = get_contract_factory("PriceLlmFieldOnly", source_file=CONTRACT_FILENAME)

    payload = dexscreener_payload_btc_base(price_usd="65000.5", liquidity_usd="999")
    vm_context.mock_web(
        DEXSCREENER_REGEX,
        {"status": 200, "body": payload},
    )
    # The LLM returns a float, ignoring the integer-only contract.
    vm_context.mock_llm(
        LLM_PRICE_PROMPT_REGEX,
        llm_response_price_float_leak(65000.5),
    )

    contract = _deploy_btc_base(factory)

    tx_receipt = contract.resolve(args=[]).transact()
    assert not tx_execution_succeeded(tx_receipt), (
        "LLM_ERROR: float-leak LLM output must NOT settle as successful execution"
    )

    state = contract.get_price(args=[]).call()
    assert state["resolved"] is False
    assert state["price_micro_usd"] == "0"


@pytest.mark.requires_mocks
@pytest.mark.parametrize("status_code", [400, 404])
def test_external_4xx_dexscreener_surfaces_external_error(vm_context, status_code):
    """DexScreener returns 4xx → contract throws [EXTERNAL]; LLM never runs.

    Deterministic external failure: _http_get_text raises [EXTERNAL] before
    the LLM is consulted. Validators independently see the same 4xx and
    agree byte-equal per the canonical helper.
    """
    factory = get_contract_factory("PriceLlmFieldOnly", source_file=CONTRACT_FILENAME)

    vm_context.mock_web(
        DEXSCREENER_REGEX,
        {"status": status_code, "body": "Not Found"},
    )
    # LLM mock is a no-op here — leader never reaches it (HTTP fails first).
    vm_context.mock_llm(
        LLM_PRICE_PROMPT_REGEX,
        llm_response_price(65000.0),
    )

    contract = _deploy_btc_base(factory)

    tx_receipt = contract.resolve(args=[]).transact()
    assert not tx_execution_succeeded(tx_receipt), (
        f"external: resolve() must fail when DexScreener returns {status_code}"
    )

    state = contract.get_price(args=[]).call()
    assert state["resolved"] is False
    assert state["price_micro_usd"] == "0"


@pytest.mark.requires_mocks
def test_transient_503_dexscreener_both_sides_transient(vm_context):
    """503 from DexScreener → leader [TRANSIENT], validator [TRANSIENT] → agree per canonical rule."""
    factory = get_contract_factory("PriceLlmFieldOnly", source_file=CONTRACT_FILENAME)

    vm_context.mock_web(
        DEXSCREENER_REGEX,
        {"status": 503, "body": "Service Unavailable"},
    )
    # LLM mock is a no-op here — leader never reaches the LLM call because
    # _http_get_text raises [TRANSIENT] first.
    vm_context.mock_llm(
        LLM_PRICE_PROMPT_REGEX,
        llm_response_price(65000.0),
    )

    contract = _deploy_btc_base(factory)

    tx_receipt = contract.resolve(args=[]).transact()
    # Execution fails; consensus may agree on the transient class but the
    # business outcome is "no state change".
    assert not tx_execution_succeeded(tx_receipt)

    state = contract.get_price(args=[]).call()
    assert state["resolved"] is False
    assert state["price_micro_usd"] == "0"
