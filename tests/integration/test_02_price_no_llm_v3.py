# Integration tests for 02_price_no_llm_v3.py
#
# Run from experiments/bradbury/:
#   gltest tests/integration/test_02_price_no_llm_v3.py -v -s --network localnet
#
# Covers:
#   - happy path: BTC base price returns a sensible value (consensus succeeds,
#     storage matches what the leader fetched within TOLERANCE_BPS)
#   - transient: DexScreener returns 503 → contract surfaces ERROR_TRANSIENT
#
# Mock cheatcodes (mock_web) are only available on localnet (GLSim / Studio).
# When this suite runs against testnet_bradbury, the `requires_mocks` marker
# in conftest auto-skips these cases.

from __future__ import annotations

import json

import pytest

from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded

from conftest import dexscreener_payload_btc_base


CONTRACT_FILENAME = "02_price_no_llm_v3.py"
DEXSCREENER_REGEX = r".*api\.dexscreener\.com/.*"

# Same scale as the contract: 1e9 micro-units per USD.
PRICE_SCALE = 1_000_000_000


def _deploy_btc_base(factory):
    """Deploy a fresh BTC/base price contract."""
    return factory.deploy(args=["BTC", "base"])


@pytest.mark.requires_mocks
def test_happy_path_btc_base_returns_sensible_value(vm_context):
    """Leader + validators agree on a BTC/base price within tolerance."""
    factory = get_contract_factory("PriceNoLlm", source_file=CONTRACT_FILENAME)

    # Stable mocked payload — every replica sees the same priceUsd.
    payload = dexscreener_payload_btc_base(
        price_usd="65000.123456789",
        liquidity_usd="12345678.9",
    )
    vm_context.mock_web(
        DEXSCREENER_REGEX,
        {"status": 200, "body": payload},
    )

    contract = _deploy_btc_base(factory)

    tx_receipt = contract.resolve(args=[]).transact()
    assert tx_execution_succeeded(tx_receipt), (
        "happy path: consensus must succeed when all replicas see the same DEX payload"
    )

    state = contract.get_price(args=[]).call()
    assert state["resolved"] is True
    assert state["symbol"] == "BTC"
    assert state["chain"] == "base"

    micro = int(state["price_micro_usd"])
    assert micro > 0, "stored price must be positive"
    # BTC realistic sanity band: between $1k and $1M.
    assert PRICE_SCALE * 1_000 < micro < PRICE_SCALE * 1_000_000, (
        f"BTC price out of realistic band: micro={micro}"
    )

    # And the formatted decimal must match prefix of the priceUsd input we mocked.
    # (Contract trims trailing zeros — only check leading characters.)
    assert state["price_usd"].startswith("65000"), state["price_usd"]


@pytest.mark.requires_mocks
def test_transient_503_surfaces_transient_error(vm_context):
    """DexScreener returns 503 → contract throws [TRANSIENT] and stays unresolved.

    The SKILL.md canonical error scheme requires every replica to independently
    hit a TRANSIENT error so the canonical _handle_leader_error can agree
    'both sides transient'. Mocking the same 503 for every replica satisfies
    that.
    """
    factory = get_contract_factory("PriceNoLlm", source_file=CONTRACT_FILENAME)

    vm_context.mock_web(
        DEXSCREENER_REGEX,
        {"status": 503, "body": "Service Unavailable"},
    )

    contract = _deploy_btc_base(factory)

    tx_receipt = contract.resolve(args=[]).transact()

    # The transient failure should NOT be a successful contract execution —
    # consensus may finalize the tx, but execution must report failure.
    assert not tx_execution_succeeded(tx_receipt), (
        "transient: resolve() must fail when DexScreener returns 503"
    )

    state = contract.get_price(args=[]).call()
    assert state["resolved"] is False, "no state should change on transient failure"
    assert state["price_micro_usd"] == "0"


@pytest.mark.requires_mocks
def test_external_no_pairs_surfaces_external_error(vm_context):
    """DexScreener returns 200 with an empty pairs[] → [EXTERNAL] error.

    EXTERNAL is the deterministic class — validators independently see the
    same empty payload, so the canonical helper agrees byte-equal on the
    error message.
    """
    factory = get_contract_factory("PriceNoLlm", source_file=CONTRACT_FILENAME)

    vm_context.mock_web(
        DEXSCREENER_REGEX,
        {"status": 200, "body": json.dumps({"pairs": []})},
    )

    contract = _deploy_btc_base(factory)

    tx_receipt = contract.resolve(args=[]).transact()
    assert not tx_execution_succeeded(tx_receipt)

    state = contract.get_price(args=[]).call()
    assert state["resolved"] is False
