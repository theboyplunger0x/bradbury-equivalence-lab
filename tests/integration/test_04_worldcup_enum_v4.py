# Direct-mode tests for 04_worldcup_enum_v4.py.
#
# Run from experiments/bradbury/:
#   gltest tests/integration/test_04_worldcup_enum_v4.py -v -s
#
# Why direct mode: gltest's mock_web cheatcode ONLY exists on the
# `direct_vm` VMContext from gltest.direct.pytest_plugin. v4 drops the
# HTML+LLM evidence pipeline entirely and consumes ONE structured public
# JSON endpoint (ESPN /summary?event=…), so there is no mock_llm here.
#
# Covers v4's enum derivation logic for the canonical outcomes:
#   - TEAM_A_WIN  (home == team_a, home_score > away_score)
#   - TEAM_B_WIN  (home == team_a, home_score < away_score)
#   - DRAW        (home_score == away_score)
#   - UNKNOWN     (ESPN returns 200 but match is NOT final yet →
#                  v4 raises [EXPECTED] 'match not yet final'; the
#                  contract's init default of outcome=UNKNOWN is what
#                  surfaces post-revert, and `needs_manual_review` is
#                  semantically false because resolved=False)
#   - EXTERNAL    (ESPN returns 4xx → [EXTERNAL], unresolved)
#   - TRANSIENT   (ESPN returns 5xx → [TRANSIENT], unresolved)

from __future__ import annotations

import pytest

from conftest import (
    espn_summary_not_final,
    espn_summary_payload,
)


CONTRACT_FILENAME = "04_worldcup_enum_v4.py"

# v4 always hits ESPN's summary endpoint with a deterministic URL.
ESPN_REGEX = r".*site\.api\.espn\.com/.*"

# Numeric event id (the contract validates ^\d{4,12}$).
ESPN_EVENT_ID = "704509"


def _deploy(direct_deploy, team_a: str = "Argentina", team_b: str = "Brazil",
            event_id: str = ESPN_EVENT_ID):
    return direct_deploy(CONTRACT_FILENAME, team_a, team_b, event_id)


# --- TEAM_A_WIN -------------------------------------------------------------

def test_team_a_win_when_team_a_is_home_and_outscores_away(direct_vm, direct_deploy):
    """ESPN: home=Argentina 2, away=Brazil 1, completed → TEAM_A_WIN."""
    payload = espn_summary_payload(
        home_name="Argentina", away_name="Brazil",
        home_score=2, away_score=1,
        state="post", completed=True,
    )
    direct_vm.mock_web(ESPN_REGEX, {"status": 200, "body": payload})

    contract = _deploy(direct_deploy)
    contract.resolve()

    state = contract.get_outcome()
    assert state["resolved"] is True
    assert state["outcome"] == "TEAM_A_WIN"
    assert state["needs_manual_review"] is False
    assert state["score"] == "2-1"


def test_team_a_win_when_team_a_is_away_and_outscores_home(direct_vm, direct_deploy):
    """ESPN: home=Brazil 1, away=Argentina 2, completed → TEAM_A_WIN.

    team_a=Argentina is on the away side; v4 must still resolve correctly
    from team_a's perspective.
    """
    payload = espn_summary_payload(
        home_name="Brazil", away_name="Argentina",
        home_score=1, away_score=2,
        state="post", completed=True,
    )
    direct_vm.mock_web(ESPN_REGEX, {"status": 200, "body": payload})

    contract = _deploy(direct_deploy)
    contract.resolve()

    state = contract.get_outcome()
    assert state["resolved"] is True
    assert state["outcome"] == "TEAM_A_WIN"
    assert state["needs_manual_review"] is False
    assert state["score"] == "1-2"


# --- TEAM_B_WIN -------------------------------------------------------------

def test_team_b_win_when_team_b_outscores_team_a(direct_vm, direct_deploy):
    """ESPN: home=Argentina 0, away=Brazil 2, completed → TEAM_B_WIN."""
    payload = espn_summary_payload(
        home_name="Argentina", away_name="Brazil",
        home_score=0, away_score=2,
        state="post", completed=True,
    )
    direct_vm.mock_web(ESPN_REGEX, {"status": 200, "body": payload})

    contract = _deploy(direct_deploy)
    contract.resolve()

    state = contract.get_outcome()
    assert state["resolved"] is True
    assert state["outcome"] == "TEAM_B_WIN"
    assert state["needs_manual_review"] is False
    assert state["score"] == "0-2"


# --- DRAW -------------------------------------------------------------------

def test_draw_when_scores_are_equal(direct_vm, direct_deploy):
    """ESPN: home=Argentina 1, away=Brazil 1, completed → DRAW."""
    payload = espn_summary_payload(
        home_name="Argentina", away_name="Brazil",
        home_score=1, away_score=1,
        state="post", completed=True,
    )
    direct_vm.mock_web(ESPN_REGEX, {"status": 200, "body": payload})

    contract = _deploy(direct_deploy)
    contract.resolve()

    state = contract.get_outcome()
    assert state["resolved"] is True
    assert state["outcome"] == "DRAW"
    assert state["needs_manual_review"] is False
    assert state["score"] == "1-1"


# --- UNKNOWN ----------------------------------------------------------------

def test_unknown_when_match_not_yet_final(direct_vm, direct_deploy):
    """ESPN responds 200 but state='in' / completed=false → [EXPECTED] 'not yet final'.

    v4 architectural choice: in-flight matches are NOT resolved — the
    contract raises [EXPECTED] so leader+validator agree byte-equal. The
    init-default outcome='UNKNOWN' is preserved on the contract state
    after the revert, and resolved stays False.
    """
    payload = espn_summary_not_final("Argentina", "Brazil")
    direct_vm.mock_web(ESPN_REGEX, {"status": 200, "body": payload})

    contract = _deploy(direct_deploy)

    with direct_vm.expect_revert("[EXPECTED]"):
        contract.resolve()

    state = contract.get_outcome()
    assert state["resolved"] is False
    assert state["outcome"] == "UNKNOWN"  # init default unchanged


# --- EXTERNAL 4xx -----------------------------------------------------------

@pytest.mark.parametrize("status_code", [400, 404])
def test_external_4xx_from_espn_surfaces_external_error(direct_vm, direct_deploy, status_code):
    """ESPN returns 4xx → contract raises [EXTERNAL]; no state change."""
    direct_vm.mock_web(
        ESPN_REGEX,
        {"status": status_code, "body": "Not Found"},
    )

    contract = _deploy(direct_deploy)

    with direct_vm.expect_revert("[EXTERNAL]"):
        contract.resolve()

    state = contract.get_outcome()
    assert state["resolved"] is False
    assert state["outcome"] == "UNKNOWN"  # init default unchanged


# --- TRANSIENT 5xx ----------------------------------------------------------

@pytest.mark.parametrize("status_code", [500, 503])
def test_transient_5xx_from_espn_surfaces_transient_error(direct_vm, direct_deploy, status_code):
    """ESPN returns 5xx → contract raises [TRANSIENT]; no state change."""
    direct_vm.mock_web(
        ESPN_REGEX,
        {"status": status_code, "body": "Service Unavailable"},
    )

    contract = _deploy(direct_deploy)

    with direct_vm.expect_revert("[TRANSIENT]"):
        contract.resolve()

    state = contract.get_outcome()
    assert state["resolved"] is False
    assert state["outcome"] == "UNKNOWN"  # init default unchanged
