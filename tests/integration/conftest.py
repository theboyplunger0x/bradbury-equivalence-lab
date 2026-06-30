# Shared pytest helpers for bradbury v3 integration suite.
#
# Run from experiments/bradbury/:
#   gltest tests/integration/ -v -s
#
# This conftest is REWRITTEN to match the REAL gltest API:
#   - There is NO `vm_context` fixture in gltest. The previous author invented
#     it. Mock cheatcodes (mock_web / mock_llm) ONLY exist on the
#     `direct_vm` VMContext provided by the `gltest.direct.pytest_plugin`
#     entry point (auto-loaded as `gltest_direct`).
#   - Tests that need mocking therefore use direct_vm + direct_deploy.
#     Integration tests against a real network (localnet/testnet) cannot
#     mock external HTTP/LLM responses through gltest at all.
#
# Helpers below are PLAIN module-level functions (no fixture wiring) —
# every test imports the payload/response builders directly.

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


# Allow `from conftest import ...` to find this file when tests run from
# any cwd that gltest may set.
_BRADBURY_DIR = Path(__file__).resolve().parent.parent.parent
if str(_BRADBURY_DIR) not in sys.path:
    sys.path.insert(0, str(_BRADBURY_DIR))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_mocks: test depends on direct-mode mock_web/mock_llm "
        "cheatcodes (only available via the direct_vm fixture in tests/direct/)",
    )
    config.addinivalue_line(
        "markers",
        "slow: long-running scenario; opt-in via `gltest -m slow`",
    )


# --- Mock payload builders --------------------------------------------------
# Centralised so test files stay readable and we never duplicate the JSON
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
    """LLM extracts the price and returns it as integer micro-USD (price * 1e9)."""
    micro = int(round(price_usd * 1_000_000_000))
    return json.dumps({"price_micro_usd": micro})


def llm_response_price_micro(price_micro_usd: int) -> str:
    """LLM returns a raw integer micro-USD value (for tests that need exact integers)."""
    return json.dumps({"price_micro_usd": int(price_micro_usd)})


def llm_response_price_float_leak(price_usd: float) -> str:
    """LLM ignores the integer-only rule and returns a float — drives [LLM_ERROR]."""
    return json.dumps({"price_micro_usd": float(price_usd)})


def llm_response_price_micro_string_float_leak(price_usd: float) -> str:
    """LLM returns a *string* containing a float — drives [LLM_ERROR] via regex.

    Exercises the STRING branch of _pick_price_micro_with_llm —
    `isinstance(val, str)` then `_DIGITS_ONLY_RE.match(s)` MUST reject because
    the string carries a decimal point (or 'e' / sign / etc).
    """
    return json.dumps({"price_micro_usd": str(price_usd * 1e9)})


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


# --- ESPN scoreboard / summary payload builders -----------------------------
# Used by 04_worldcup_enum_v4 integration tests. Mirrors the real ESPN shape:
#   data["header"]["competitions"][0] = {
#       "status": {"type": {"state": "post"|"in"|"pre", "completed": bool}},
#       "competitors": [
#           {"homeAway": "home", "team": {"displayName": "Argentina"}, "score": "2"},
#           {"homeAway": "away", "team": {"displayName": "Brazil"},    "score": "1"},
#       ],
#   }
# Both leader and validator parse the SAME path and derive the SAME enum.

def espn_summary_payload(
    home_name: str = "Argentina",
    away_name: str = "Brazil",
    home_score: int = 2,
    away_score: int = 1,
    state: str = "post",
    completed: bool = True,
) -> str:
    """ESPN /summary?event=… shape — completed match by default."""
    return json.dumps({
        "header": {
            "competitions": [
                {
                    "status": {
                        "type": {
                            "state": state,
                            "completed": completed,
                        }
                    },
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {"displayName": home_name},
                            "score": str(home_score),
                        },
                        {
                            "homeAway": "away",
                            "team": {"displayName": away_name},
                            "score": str(away_score),
                        },
                    ],
                }
            ]
        }
    })


def espn_summary_not_final(
    home_name: str = "Argentina",
    away_name: str = "Brazil",
) -> str:
    """ESPN summary where the match is in-progress (state=in, completed=false).

    Drives the contract into the EXPECTED 'match not yet final' branch, which
    in v4 lands as outcome=UNKNOWN at the validator level (revert path), so
    the test asserts on the [EXPECTED] revert message instead of a state read.
    """
    return espn_summary_payload(
        home_name=home_name,
        away_name=away_name,
        home_score=0,
        away_score=0,
        state="in",
        completed=False,
    )
