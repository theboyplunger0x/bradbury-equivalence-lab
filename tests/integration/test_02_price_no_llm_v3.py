# Direct-mode tests for 02_price_no_llm_v3.py.
#
# Run from experiments/bradbury/:
#   gltest tests/integration/test_02_price_no_llm_v3.py -v -s
#
# Why direct mode: gltest's mock_web cheatcode ONLY exists on the
# `direct_vm` VMContext from gltest.direct.pytest_plugin. The previous
# `vm_context` fixture was fictional — see the rewritten conftest.
#
# Covers (preserved intent from the previous integration suite):
#   - happy path: BTC base price returns a sensible value
#   - transient: DexScreener 503 → [TRANSIENT], no state change
#   - external 200 + empty pairs[] → [EXTERNAL], no state change
#   - external 4xx (400, 404) → [EXTERNAL], no state change

from __future__ import annotations

import json

import pytest

from conftest import dexscreener_payload_btc_base


CONTRACT_FILENAME = "02_price_no_llm_v3.py"
DEXSCREENER_REGEX = r".*api\.dexscreener\.com/.*"

# Same scale as the contract: 1e9 micro-units per USD.
PRICE_SCALE = 1_000_000_000


def _deploy_btc_base(direct_deploy):
    """Deploy a fresh BTC/base price contract via direct mode."""
    return direct_deploy(CONTRACT_FILENAME, "BTC", "base")


def test_happy_path_btc_base_returns_sensible_value(direct_vm, direct_deploy):
    """Mocked DexScreener payload → contract resolves with sensible BTC price."""
    payload = dexscreener_payload_btc_base(
        price_usd="65000.123456789",
        liquidity_usd="12345678.9",
    )
    direct_vm.mock_web(
        DEXSCREENER_REGEX,
        {"status": 200, "body": payload},
    )

    contract = _deploy_btc_base(direct_deploy)
    contract.resolve()

    state = contract.get_price()
    assert state["resolved"] is True
    assert state["symbol"] == "BTC"
    assert state["chain"] == "base"

    micro = int(state["price_micro_usd"])
    assert micro > 0, "stored price must be positive"
    # BTC realistic sanity band: between $1k and $1M.
    assert PRICE_SCALE * 1_000 < micro < PRICE_SCALE * 1_000_000, (
        f"BTC price out of realistic band: micro={micro}"
    )

    # Formatted decimal must reflect the input we mocked.
    assert state["price_usd"].startswith("65000"), state["price_usd"]


def test_transient_503_surfaces_transient_error(direct_vm, direct_deploy):
    """DexScreener returns 503 → contract raises [TRANSIENT]; state unchanged."""
    direct_vm.mock_web(
        DEXSCREENER_REGEX,
        {"status": 503, "body": "Service Unavailable"},
    )

    contract = _deploy_btc_base(direct_deploy)

    with direct_vm.expect_revert("[TRANSIENT]"):
        contract.resolve()

    state = contract.get_price()
    assert state["resolved"] is False, "no state should change on transient failure"
    assert state["price_micro_usd"] == "0"


def test_external_no_pairs_surfaces_external_error(direct_vm, direct_deploy):
    """DexScreener returns 200 with an empty pairs[] → [EXTERNAL]; state unchanged."""
    direct_vm.mock_web(
        DEXSCREENER_REGEX,
        {"status": 200, "body": json.dumps({"pairs": []})},
    )

    contract = _deploy_btc_base(direct_deploy)

    with direct_vm.expect_revert("[EXTERNAL]"):
        contract.resolve()

    state = contract.get_price()
    assert state["resolved"] is False
    assert state["price_micro_usd"] == "0"


@pytest.mark.parametrize("status_code", [400, 404])
def test_external_4xx_surfaces_external_error(direct_vm, direct_deploy, status_code):
    """DexScreener returns 4xx → contract raises [EXTERNAL]; state unchanged."""
    direct_vm.mock_web(
        DEXSCREENER_REGEX,
        {"status": status_code, "body": "Not Found"},
    )

    contract = _deploy_btc_base(direct_deploy)

    with direct_vm.expect_revert("[EXTERNAL]"):
        contract.resolve()

    state = contract.get_price()
    assert state["resolved"] is False, "no state should change on external 4xx failure"
    assert state["price_micro_usd"] == "0"
