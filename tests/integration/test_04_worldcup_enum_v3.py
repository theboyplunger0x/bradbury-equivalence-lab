# Integration tests for 04_worldcup_enum_v3.py
#
# Run from experiments/bradbury/:
#   gltest tests/integration/test_04_worldcup_enum_v3.py -v -s --network localnet
#
# Covers the 5 outcome cases the contract supports:
#   - TEAM_A_WIN  (deterministic regex hits Full-time score 2-1)
#   - TEAM_B_WIN  (deterministic regex hits FT 0-2)
#   - DRAW        (deterministic regex hits Final 1-1)
#   - UNKNOWN     (no structured score AND LLM not confident → enum=UNKNOWN,
#                  resolved=True, needs_manual_review=True)
#   - TRANSIENT   (every evidence URL returns 503 → contract surfaces
#                  ERROR_TRANSIENT, resolved stays False)
#
# Step 1 of the v3 pipeline is regex over evidence text — byte-deterministic
# across leader and validator. Steps 2-3 only fire when Step 1 fails.

from __future__ import annotations

import pytest

from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded

from conftest import (
    evidence_payload_draw,
    evidence_payload_team_a_win,
    evidence_payload_team_b_win,
    evidence_payload_unknown,
    llm_response_outcome_garbage,
    llm_response_unknown,
)


CONTRACT_FILENAME = "04_worldcup_enum_v3.py"

# Two evidence URLs so _fetch_all_evidence has more than one source. The
# contract requires every URL to start with https:// and uses up to 6.
EVIDENCE_URLS = [
    "https://www.bbc.com/sport/football/some-match",
    "https://www.espn.com/soccer/match/_/gameId/000000",
]

# Per-URL regexes so each source can return tailored evidence.
EVIDENCE_REGEX_BBC = r".*bbc\.com.*"
EVIDENCE_REGEX_ESPN = r".*espn\.com.*"

LLM_OUTCOME_PROMPT_REGEX = r".*settling a head-to-head match market.*"


def _leader_error_payload(tx_receipt) -> str:
    """Best-effort extraction of the leader receipt's error payload string.

    Mirrors the helpers in test_02 / test_03 — see those files for the
    rationale. Returns "" if the structure is missing so the caller can skip
    the assertion cleanly rather than fake a pass.
    """
    try:
        receipts = tx_receipt["consensus_data"]["leader_receipt"]
        if not receipts:
            return ""
        result = receipts[0].get("result") or {}
        if isinstance(result, dict):
            payload = result.get("payload")
            if isinstance(payload, str):
                return payload
        return str(result)
    except (KeyError, TypeError, IndexError):
        return ""


def _deploy(factory, team_a: str = "Argentina", team_b: str = "Brazil"):
    return factory.deploy(args=[team_a, team_b, list(EVIDENCE_URLS)])


# --- TEAM_A_WIN -------------------------------------------------------------

@pytest.mark.requires_mocks
def test_team_a_win_from_structured_score(vm_context):
    """Both evidence sources carry 'Full time: 2-1' → TEAM_A_WIN deterministically."""
    factory = get_contract_factory("WorldcupEnum", source_file=CONTRACT_FILENAME)

    body = evidence_payload_team_a_win("Argentina", "Brazil", 2, 1)
    vm_context.mock_web(EVIDENCE_REGEX_BBC, {"status": 200, "body": body})
    vm_context.mock_web(EVIDENCE_REGEX_ESPN, {"status": 200, "body": body})

    contract = _deploy(factory)
    tx_receipt = contract.resolve(args=[]).transact()
    assert tx_execution_succeeded(tx_receipt)

    state = contract.get_outcome(args=[]).call()
    assert state["resolved"] is True
    assert state["outcome"] == "TEAM_A_WIN"
    assert state["needs_manual_review"] is False
    # Advisory fields captured on the leader run.
    assert state["score"] == "2-1"


# --- TEAM_B_WIN -------------------------------------------------------------

@pytest.mark.requires_mocks
def test_team_b_win_from_structured_score(vm_context):
    """Evidence with 'FT: 0-2' → TEAM_B_WIN."""
    factory = get_contract_factory("WorldcupEnum", source_file=CONTRACT_FILENAME)

    body = evidence_payload_team_b_win("Argentina", "Brazil")
    vm_context.mock_web(EVIDENCE_REGEX_BBC, {"status": 200, "body": body})
    vm_context.mock_web(EVIDENCE_REGEX_ESPN, {"status": 200, "body": body})

    contract = _deploy(factory)
    tx_receipt = contract.resolve(args=[]).transact()
    assert tx_execution_succeeded(tx_receipt)

    state = contract.get_outcome(args=[]).call()
    assert state["resolved"] is True
    assert state["outcome"] == "TEAM_B_WIN"
    assert state["needs_manual_review"] is False
    assert state["score"] == "0-2"


# --- DRAW -------------------------------------------------------------------

@pytest.mark.requires_mocks
def test_draw_from_structured_score(vm_context):
    """Evidence with 'Final: 1-1' → DRAW."""
    factory = get_contract_factory("WorldcupEnum", source_file=CONTRACT_FILENAME)

    body = evidence_payload_draw("Argentina", "Brazil")
    vm_context.mock_web(EVIDENCE_REGEX_BBC, {"status": 200, "body": body})
    vm_context.mock_web(EVIDENCE_REGEX_ESPN, {"status": 200, "body": body})

    contract = _deploy(factory)
    tx_receipt = contract.resolve(args=[]).transact()
    assert tx_execution_succeeded(tx_receipt)

    state = contract.get_outcome(args=[]).call()
    assert state["resolved"] is True
    assert state["outcome"] == "DRAW"
    assert state["needs_manual_review"] is False
    assert state["score"] == "1-1"


# --- UNKNOWN ----------------------------------------------------------------

@pytest.mark.requires_mocks
def test_unknown_when_no_structured_score_and_llm_not_confident(vm_context):
    """No regex match + LLM returns confident=false → UNKNOWN.

    UNKNOWN is a valid enum value: the contract resolves (so the consensus
    finalizes) but flags needs_manual_review=True.
    """
    factory = get_contract_factory("WorldcupEnum", source_file=CONTRACT_FILENAME)

    body = evidence_payload_unknown()
    vm_context.mock_web(EVIDENCE_REGEX_BBC, {"status": 200, "body": body})
    vm_context.mock_web(EVIDENCE_REGEX_ESPN, {"status": 200, "body": body})

    # LLM fallback fires; must NOT be confident.
    vm_context.mock_llm(
        LLM_OUTCOME_PROMPT_REGEX,
        llm_response_unknown(),
    )

    contract = _deploy(factory)
    tx_receipt = contract.resolve(args=[]).transact()
    assert tx_execution_succeeded(tx_receipt), (
        "UNKNOWN is a valid in-enum outcome — consensus must succeed"
    )

    state = contract.get_outcome(args=[]).call()
    assert state["resolved"] is True
    assert state["outcome"] == "UNKNOWN"
    assert state["needs_manual_review"] is True


# --- TRANSIENT --------------------------------------------------------------

@pytest.mark.requires_mocks
def test_transient_when_all_sources_503(vm_context):
    """Every evidence URL returns 503 → contract surfaces ERROR_TRANSIENT.

    _fetch_all_evidence raises [TRANSIENT] when zero snippets were collected
    AND at least one source hit a 5xx (no externals). Per the canonical
    SKILL.md rule, the validator independently sees the same condition and
    agrees 'both sides transient' — but the execution itself failed, so no
    state change.
    """
    factory = get_contract_factory("WorldcupEnum", source_file=CONTRACT_FILENAME)

    vm_context.mock_web(
        EVIDENCE_REGEX_BBC,
        {"status": 503, "body": "Service Unavailable"},
    )
    vm_context.mock_web(
        EVIDENCE_REGEX_ESPN,
        {"status": 503, "body": "Service Unavailable"},
    )

    contract = _deploy(factory)
    tx_receipt = contract.resolve(args=[]).transact()
    assert not tx_execution_succeeded(tx_receipt)

    state = contract.get_outcome(args=[]).call()
    assert state["resolved"] is False
    assert state["outcome"] == "UNKNOWN"  # init default


# --- EXTERNAL 4xx -----------------------------------------------------------

@pytest.mark.requires_mocks
@pytest.mark.parametrize("status_code", [400, 404])
def test_external_4xx_evidence_surfaces_external_error(vm_context, status_code):
    """One evidence URL returns 4xx and the other returns 4xx → [EXTERNAL].

    _fetch_all_evidence raises [EXTERNAL] when zero snippets were collected
    AND every failure was a deterministic 4xx (no transients). Validators
    independently see the same condition; the canonical helper agrees
    byte-equal on the EXTERNAL-prefixed message.

    We mock at least one URL returning 4xx; the other also 4xx so the
    aggregated condition is unambiguously "external-only".
    """
    factory = get_contract_factory("WorldcupEnum", source_file=CONTRACT_FILENAME)

    vm_context.mock_web(
        EVIDENCE_REGEX_BBC,
        {"status": status_code, "body": "Not Found"},
    )
    vm_context.mock_web(
        EVIDENCE_REGEX_ESPN,
        {"status": status_code, "body": "Not Found"},
    )

    contract = _deploy(factory)
    tx_receipt = contract.resolve(args=[]).transact()
    assert not tx_execution_succeeded(tx_receipt), (
        f"external: resolve() must fail when every evidence URL returns {status_code}"
    )

    state = contract.get_outcome(args=[]).call()
    assert state["resolved"] is False
    assert state["outcome"] == "UNKNOWN"  # init default unchanged

    # Tighten: SKILL.md canonical scheme requires the leader to surface
    # "[EXTERNAL]" so deterministic validators byte-match. Skip cleanly if
    # the harness build does not expose the leader payload — faking is worse
    # than missing coverage here.
    err = _leader_error_payload(tx_receipt)
    if err:
        assert "[EXTERNAL]" in err, (
            f"expected [EXTERNAL] prefix in leader payload for {status_code}, "
            f"got: {err!r}"
        )


# --- LLM_ERROR --------------------------------------------------------------

@pytest.mark.requires_mocks
def test_llm_error_garbage_fallback_output_blocks_consensus(vm_context):
    """No structured score + LLM returns garbage → [LLM_ERROR], consensus fails.

    Forces the Step 2 LLM fallback by serving evidence with NO regex-parseable
    score, then makes the LLM return a malformed JSON shape missing the
    required `outcome` key. _llm_fallback_outcome raises [LLM_ERROR]; the
    canonical _handle_leader_error DISAGREES, forcing leader rotation, and
    after retries the tx is reported as failed execution with no state
    change.
    """
    factory = get_contract_factory("WorldcupEnum", source_file=CONTRACT_FILENAME)

    body = evidence_payload_unknown()
    vm_context.mock_web(EVIDENCE_REGEX_BBC, {"status": 200, "body": body})
    vm_context.mock_web(EVIDENCE_REGEX_ESPN, {"status": 200, "body": body})

    # LLM fallback returns garbage (missing `outcome`) — triggers LLM_ERROR.
    vm_context.mock_llm(
        LLM_OUTCOME_PROMPT_REGEX,
        llm_response_outcome_garbage(),
    )

    contract = _deploy(factory)
    tx_receipt = contract.resolve(args=[]).transact()
    assert not tx_execution_succeeded(tx_receipt), (
        "LLM_ERROR: garbage LLM fallback output must NOT settle as successful execution"
    )

    state = contract.get_outcome(args=[]).call()
    assert state["resolved"] is False
    assert state["outcome"] == "UNKNOWN"  # init default unchanged
