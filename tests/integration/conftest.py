# Shared pytest helpers for bradbury v3 gltest integration suite.
#
# Run from experiments/bradbury/:
#   gltest tests/integration/ -v -s --network localnet
#
# These helpers wrap gltest's mock cheatcodes (mock_web / mock_llm) which
# are available on GLSim + Studio localnet but NOT on Bradbury testnet.
# Tests that depend on mocks are auto-skipped on testnet via the
# `requires_mocks` marker.

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


# Allow `from gltest...` imports to work even before the package is installed.
# Real gltest install: `pip install genlayer-test[sim]`.
_BRADBURY_DIR = Path(__file__).resolve().parent.parent.parent
if str(_BRADBURY_DIR) not in sys.path:
    sys.path.insert(0, str(_BRADBURY_DIR))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_mocks: test depends on mock_web/mock_llm cheatcodes "
        "(localnet/GLSim/Studio only — auto-skipped on testnet networks)",
    )
    config.addinivalue_line(
        "markers",
        "slow: long-running scenario; opt-in via `gltest -m slow`",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip mock-dependent tests when running against a real testnet."""
    network = (config.getoption("--network", default=None) or "").lower()
    if "testnet" in network:
        skip_mocks = pytest.mark.skip(
            reason=f"mock cheatcodes unavailable on network={network}"
        )
        for item in items:
            if "requires_mocks" in item.keywords:
                item.add_marker(skip_mocks)


def pytest_addoption(parser):
    # gltest already registers --network; only add if missing so we don't
    # collide when running under the real harness.
    existing = {opt for group in parser._groups for opt in group.options}
    if "--network" not in {o.names()[0] for g in parser._groups for o in g.options}:
        try:
            parser.addoption("--network", action="store", default=None)
        except ValueError:
            # Already registered by gltest plugin — fine.
            pass


# --- Mock payload builders --------------------------------------------------
# Centralized so test files stay readable and we never duplicate the JSON
# shape DexScreener actually returns.

def dexscreener_payload_btc_base(price_usd: str = "65000.123456789",
                                 liquidity_usd: str = "12345678.9") -> str:
    """Realistic DexScreener payload: WBTC/USDC on Base with deep liquidity."""
    return json.dumps({
        "schemaVersion": "1.0.0",
        "pairs": [
            {
                "chainId": "base",
                "dexId": "uniswap",
                "pairAddress": "0xabc000000000000000000000000000000000abcd",
                "baseToken": {
                    "address": "0xcbBTC0000000000000000000000000000000cbbtc",
                    "name": "Coinbase Wrapped BTC",
                    "symbol": "BTC",
                },
                "quoteToken": {"symbol": "USDC"},
                "priceUsd": price_usd,
                "liquidity": {"usd": liquidity_usd},
            }
        ],
    })


def dexscreener_payload_no_pairs() -> str:
    return json.dumps({"schemaVersion": "1.0.0", "pairs": []})


def evidence_payload_team_a_win(team_a: str = "Argentina",
                                team_b: str = "Brazil",
                                score_a: int = 2,
                                score_b: int = 1) -> str:
    return (
        f"<html><body>"
        f"<h1>{team_a} vs {team_b}</h1>"
        f"<p>Full time: {score_a}-{score_b}. {team_a} wins the match.</p>"
        f"</body></html>"
    )


def evidence_payload_draw(team_a: str = "Argentina",
                          team_b: str = "Brazil") -> str:
    return (
        f"<html><body><h1>{team_a} vs {team_b}</h1>"
        f"<p>Final: 1-1 after full time. The match ends in a draw.</p>"
        f"</body></html>"
    )


def evidence_payload_team_b_win(team_a: str = "Argentina",
                                team_b: str = "Brazil") -> str:
    return (
        f"<html><body><h1>{team_a} vs {team_b}</h1>"
        f"<p>FT: 0-2. {team_b} secures the win.</p></body></html>"
    )


def evidence_payload_unknown() -> str:
    """Page that contains NO structured score and NO clear outcome.

    Used to drive _parse_structured_score → None and _llm_fallback_outcome
    → UNKNOWN (confident=False).
    """
    return (
        "<html><body>"
        "<h1>Match preview</h1>"
        "<p>The teams will meet later this week. Kickoff time TBD.</p>"
        "</body></html>"
    )


# --- LLM mock builders ------------------------------------------------------

def llm_response_price(price_usd: float) -> str:
    """LLM extracts the price and returns it as integer micro-USD (price * 1e9).

    Contract 03 v3 requires integer-only output under the key
    `price_micro_usd`. We multiply here so callers can keep writing tests
    in human-readable USD.
    """
    micro = int(round(price_usd * 1_000_000_000))
    return json.dumps({"price_micro_usd": micro})


def llm_response_price_micro(price_micro_usd: int) -> str:
    """LLM returns a raw integer micro-USD value (for tests that need exact integers)."""
    return json.dumps({"price_micro_usd": int(price_micro_usd)})


def llm_response_price_float_leak(price_usd: float) -> str:
    """LLM ignores the integer-only rule and returns a float — drives [LLM_ERROR]."""
    # Emit as a JSON number that explicitly carries a decimal point so the
    # downstream regex digit-only check rejects it.
    return json.dumps({"price_micro_usd": float(price_usd)})


def llm_response_garbage() -> str:
    """LLM returned a malformed shape — drives [LLM_ERROR] path."""
    return json.dumps({"not_the_right_field": "lol"})


def llm_response_outcome_garbage() -> str:
    """LLM fallback for contract 04 returns garbage missing `outcome` — drives [LLM_ERROR]."""
    return json.dumps({"not_the_right_field": "lol"})


def llm_response_outcome(outcome: str, score: str = "", confident: bool = True) -> str:
    return json.dumps({"outcome": outcome, "score": score, "confident": confident})


def llm_response_unknown() -> str:
    return json.dumps({"outcome": "UNKNOWN", "score": "", "confident": False})
