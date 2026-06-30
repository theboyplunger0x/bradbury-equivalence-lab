# Direct-mode tests for 04_worldcup_enum_v3.py.
#
# Run from experiments/bradbury/:
#   gltest tests/integration/test_04_worldcup_enum_v3.py -v -s
#
# Why direct mode: gltest's mock_web/mock_llm cheatcodes ONLY exist on
# the `direct_vm` VMContext from gltest.direct.pytest_plugin. The
# previous `vm_context` fixture was fictional — see the rewritten conftest.
#
# Covers the 5 outcome cases the contract supports:
#   - TEAM_A_WIN  (deterministic regex hits Full-time score 2-1)
#   - TEAM_B_WIN  (deterministic regex hits FT 0-2)
#   - DRAW        (deterministic regex hits Final 1-1)
#   - UNKNOWN     (no structured score AND LLM not confident → enum=UNKNOWN,
#                  resolved=True, needs_manual_review=True)
#   - TRANSIENT   (every evidence URL returns 503 → [TRANSIENT], unresolved)
#   - EXTERNAL    (every evidence URL returns 4xx → [EXTERNAL], unresolved)
#   - LLM_ERROR   (no structured score AND LLM returns garbage → [LLM_ERROR])

from __future__ import annotations

import pytest

from conftest import (
    evidence_payload_draw,
    evidence_payload_team_a_win,
    evidence_payload_team_b_win,
    evidence_payload_unknown,
    llm_response_outcome_garbage,
    llm_response_unknown,
)


CONTRACT_FILENAME = "04_worldcup_enum_v3.py"

# Two evidence URLs so _fetch_all_evidence has more than one source.
EVIDENCE_URLS = [
    "https://www.bbc.com/sport/football/some-match",
    "https://www.espn.com/soccer/match/_/gameId/000000",
]

# Per-URL regexes so each source can return tailored evidence.
EVIDENCE_REGEX_BBC = r".*bbc\.com.*"
EVIDENCE_REGEX_ESPN = r".*espn\.com.*"

LLM_OUTCOME_PROMPT_REGEX = r".*settling a head-to-head match market.*"


def _deploy(direct_deploy, team_a: str = "Argentina", team_b: str = "Brazil"):
    return direct_deploy(CONTRACT_FILENAME, team_a, team_b, list(EVIDENCE_URLS))


# --- TEAM_A_WIN -------------------------------------------------------------

def test_team_a_win_from_structured_score(direct_vm, direct_deploy):
    """Both evidence sources carry 'Full time: 2-1' → TEAM_A_WIN deterministically."""
    body = evidence_payload_team_a_win("Argentina", "Brazil", 2, 1)
    direct_vm.mock_web(EVIDENCE_REGEX_BBC, {"status": 200, "body": body})
    direct_vm.mock_web(EVIDENCE_REGEX_ESPN, {"status": 200, "body": body})

    contract = _deploy(direct_deploy)
    contract.resolve()

    state = contract.get_outcome()
    assert state["resolved"] is True
    assert state["outcome"] == "TEAM_A_WIN"
    assert state["needs_manual_review"] is False
    assert state["score"] == "2-1"


# --- TEAM_B_WIN -------------------------------------------------------------

def test_team_b_win_from_structured_score(direct_vm, direct_deploy):
    """Evidence with 'FT: 0-2' → TEAM_B_WIN."""
    body = evidence_payload_team_b_win("Argentina", "Brazil")
    direct_vm.mock_web(EVIDENCE_REGEX_BBC, {"status": 200, "body": body})
    direct_vm.mock_web(EVIDENCE_REGEX_ESPN, {"status": 200, "body": body})

    contract = _deploy(direct_deploy)
    contract.resolve()

    state = contract.get_outcome()
    assert state["resolved"] is True
    assert state["outcome"] == "TEAM_B_WIN"
    assert state["needs_manual_review"] is False
    assert state["score"] == "0-2"


# --- DRAW -------------------------------------------------------------------

def test_draw_from_structured_score(direct_vm, direct_deploy):
    """Evidence with 'Final: 1-1' → DRAW."""
    body = evidence_payload_draw("Argentina", "Brazil")
    direct_vm.mock_web(EVIDENCE_REGEX_BBC, {"status": 200, "body": body})
    direct_vm.mock_web(EVIDENCE_REGEX_ESPN, {"status": 200, "body": body})

    contract = _deploy(direct_deploy)
    contract.resolve()

    state = contract.get_outcome()
    assert state["resolved"] is True
    assert state["outcome"] == "DRAW"
    assert state["needs_manual_review"] is False
    assert state["score"] == "1-1"


# --- UNKNOWN ----------------------------------------------------------------

def test_unknown_when_no_structured_score_and_llm_not_confident(direct_vm, direct_deploy):
    """No regex match + LLM returns confident=false → UNKNOWN (resolved=True).

    UNKNOWN is a valid enum value: the contract resolves so consensus
    finalizes, but flags needs_manual_review=True.
    """
    body = evidence_payload_unknown()
    direct_vm.mock_web(EVIDENCE_REGEX_BBC, {"status": 200, "body": body})
    direct_vm.mock_web(EVIDENCE_REGEX_ESPN, {"status": 200, "body": body})
    direct_vm.mock_llm(
        LLM_OUTCOME_PROMPT_REGEX,
        llm_response_unknown(),
    )

    contract = _deploy(direct_deploy)
    contract.resolve()

    state = contract.get_outcome()
    assert state["resolved"] is True
    assert state["outcome"] == "UNKNOWN"
    assert state["needs_manual_review"] is True


# --- TRANSIENT --------------------------------------------------------------

def test_transient_when_all_sources_503(direct_vm, direct_deploy):
    """Every evidence URL returns 503 → contract raises [TRANSIENT]."""
    direct_vm.mock_web(
        EVIDENCE_REGEX_BBC,
        {"status": 503, "body": "Service Unavailable"},
    )
    direct_vm.mock_web(
        EVIDENCE_REGEX_ESPN,
        {"status": 503, "body": "Service Unavailable"},
    )

    contract = _deploy(direct_deploy)

    with direct_vm.expect_revert("[TRANSIENT]"):
        contract.resolve()

    state = contract.get_outcome()
    assert state["resolved"] is False
    assert state["outcome"] == "UNKNOWN"  # init default


# --- EXTERNAL 4xx -----------------------------------------------------------

@pytest.mark.parametrize("status_code", [400, 404])
def test_external_4xx_evidence_surfaces_external_error(direct_vm, direct_deploy, status_code):
    """Both evidence URLs return 4xx → [EXTERNAL]; no state change."""
    direct_vm.mock_web(
        EVIDENCE_REGEX_BBC,
        {"status": status_code, "body": "Not Found"},
    )
    direct_vm.mock_web(
        EVIDENCE_REGEX_ESPN,
        {"status": status_code, "body": "Not Found"},
    )

    contract = _deploy(direct_deploy)

    with direct_vm.expect_revert("[EXTERNAL]"):
        contract.resolve()

    state = contract.get_outcome()
    assert state["resolved"] is False
    assert state["outcome"] == "UNKNOWN"  # init default unchanged


# --- LLM_ERROR --------------------------------------------------------------

def test_llm_error_garbage_fallback_output_blocks_consensus(direct_vm, direct_deploy):
    """No structured score + LLM returns garbage → [LLM_ERROR]; no state change."""
    body = evidence_payload_unknown()
    direct_vm.mock_web(EVIDENCE_REGEX_BBC, {"status": 200, "body": body})
    direct_vm.mock_web(EVIDENCE_REGEX_ESPN, {"status": 200, "body": body})
    direct_vm.mock_llm(
        LLM_OUTCOME_PROMPT_REGEX,
        llm_response_outcome_garbage(),
    )

    contract = _deploy(direct_deploy)

    with direct_vm.expect_revert("[LLM_ERROR]"):
        contract.resolve()

    state = contract.get_outcome()
    assert state["resolved"] is False
    assert state["outcome"] == "UNKNOWN"  # init default unchanged
