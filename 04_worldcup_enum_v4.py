# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# Lab contract 04 v4 — World Cup outcome oracle, STRUCTURED API edition.
#
# ARCHITECTURAL PIVOT vs v3 (Codex-confirmed):
#
#   v3 fetched HTML evidence URLs and ran a 2-step pipeline:
#     Step 1: regex over raw HTML for "Full time: 2-1" patterns
#     Step 2: LLM fallback if regex missed
#
#   That works but is fragile in two ways:
#     - HTML markup churn breaks the regex without warning.
#     - The LLM fallback re-introduces non-determinism at the very step
#       where consensus is hardest (each validator running an LLM).
#
#   v4 drops HTML+LLM entirely and consumes a STRUCTURED PUBLIC JSON
#   API: ESPN's free, no-API-key scoreboard. Specifically, the per-event
#   "summary" endpoint:
#
#     https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary?event={espn_event_id}
#
#   The response is a deterministic JSON document with explicit fields:
#     header.competitions[0].competitors[] → home/away team + score
#     header.competitions[0].status.type.state → "post" when match is final
#     header.competitions[0].status.type.completed → bool
#
#   Both leader and validator parse the SAME JSON via the SAME path and
#   derive the SAME enum primitive — no LLM, no regex over HTML.
#
#   Determinism budget: gl.nondet.web.get is still non-deterministic
#   (network), but the parsed JSON is stable across validators for a
#   completed match (ESPN's response for a final game does not change).
#   For in-flight matches we return UNKNOWN and refuse to resolve — the
#   contract surfaces a "not yet final" error in the EXPECTED class so
#   leader+validator agree byte-equal.
#
#   4-prefix error scheme:
#     - EXTERNAL  → 4xx from ESPN (bad event id / wrong sport path).
#     - TRANSIENT → 5xx from ESPN (provider blip — retry).
#     - EXPECTED  → match-not-yet-final (deterministic from JSON state).
#     - LLM_ERROR → unused in v4 (no LLM path).
#
#   Single self-contained file: NO local imports. Inlined helpers and
#   the canonical _handle_leader_error are copied verbatim from v3's
#   inlined block. Per Phase 5c sandbox lesson: GenLayer validators
#   cannot import sibling local modules.

from genlayer import *
import json
import re


# --- Inlined GenLayer helpers (canonical anti-pattern fixes from SKILL.md) ----
# Inlined because GenLayer contracts run in a per-validator sandbox that does
# NOT have access to sibling local modules at validator load time. Importing
# from a sibling helpers module raises ImportError on every validator and the
# deploy comes back FINISHED_WITH_ERROR. Keep this block in lock-step with the
# source-of-truth helpers file (experiments/bradbury directory).

# Canonical error prefix scheme (per SKILL.md errorPrefixScheme). Each prefix
# tags a different deterministic / non-deterministic class so validators know
# how to compare their own error against the leader's.
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


VALID_OUTCOMES = ["TEAM_A_WIN", "TEAM_B_WIN", "DRAW", "UNKNOWN"]
VALID_OUTCOMES_SET = set(VALID_OUTCOMES)
RESOLVABLE_OUTCOMES = {"TEAM_A_WIN", "TEAM_B_WIN", "DRAW"}


# --- ESPN endpoint config ----------------------------------------------------
# Public, no-API-key, free. The summary endpoint returns one specific event's
# header + competitors + status. We construct the URL deterministically from
# the constructor arg `espn_event_id` so leader and validator hit the SAME URL.
ESPN_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/"
    "summary?event={event_id}"
)

# ESPN event id is numeric (e.g. "704509"). Be strict: anything else is an
# EXPECTED contract-layer error so leader+validator agree byte-equal.
_EVENT_ID_RE = re.compile(r"^\d{4,12}$")


def _clean(value: str, max_len: int) -> str:
    if value is None:
        return ""
    return str(value).strip()[:max_len]


def _validate_event_id(event_id: str) -> str:
    cleaned = _clean(event_id, 16)
    if not _EVENT_ID_RE.match(cleaned):
        raise gl.vm.UserError(
            f"{ERROR_EXPECTED} invalid espn_event_id (must be numeric)"
        )
    return cleaned


def _http_get_json(url: str) -> dict:
    """Fetch JSON from ESPN. Maps HTTP status to canonical error prefixes."""
    response = gl.nondet.web.get(url)
    status = getattr(response, "status", 200)
    if 400 <= status < 500:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} ESPN returned {status}")
    if status >= 500:
        raise gl.vm.UserError(f"{ERROR_TRANSIENT} ESPN returned {status}")
    body = getattr(response, "body", b"")
    try:
        if isinstance(body, bytes):
            text = body.decode("utf-8", errors="ignore")
        else:
            text = str(body)
    except (UnicodeError, ValueError) as e:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} ESPN body unreadable: {e}")
    if not text:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} ESPN returned empty body")
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as e:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} ESPN body not JSON: {e}")
    if not isinstance(data, dict):
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} ESPN body not a JSON object")
    return data


def _extract_competition(data: dict) -> dict:
    """Walk into the ESPN summary doc and return the competition dict.

    Shape: data["header"]["competitions"][0]
    Raises EXTERNAL if the shape is wrong (deterministic from the bytes).
    """
    header = data.get("header")
    if not isinstance(header, dict):
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} ESPN missing header")
    competitions = header.get("competitions")
    if not isinstance(competitions, list) or not competitions:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} ESPN missing competitions")
    comp = competitions[0]
    if not isinstance(comp, dict):
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} ESPN competition not object")
    return comp


def _is_final(comp: dict) -> bool:
    """True iff the match is officially completed.

    ESPN exposes both a `state` string ("pre" | "in" | "post") and a
    `completed` boolean. We require BOTH "post" AND completed=true to
    avoid edge cases like "post" set during half-time abandonment.
    """
    status = comp.get("status")
    if not isinstance(status, dict):
        return False
    type_obj = status.get("type")
    if not isinstance(type_obj, dict):
        return False
    state = _clean(type_obj.get("state", ""), 16).lower()
    completed = type_obj.get("completed", False) is True
    return state == "post" and completed


def _extract_competitors(comp: dict):
    """Return (home_dict, away_dict). Raises EXTERNAL on malformed shape."""
    competitors = comp.get("competitors")
    if not isinstance(competitors, list) or len(competitors) != 2:
        raise gl.vm.UserError(
            f"{ERROR_EXTERNAL} ESPN expected 2 competitors"
        )
    home = None
    away = None
    for c in competitors:
        if not isinstance(c, dict):
            raise gl.vm.UserError(f"{ERROR_EXTERNAL} ESPN competitor not object")
        side = _clean(c.get("homeAway", ""), 8).lower()
        if side == "home":
            home = c
        elif side == "away":
            away = c
    if home is None or away is None:
        raise gl.vm.UserError(
            f"{ERROR_EXTERNAL} ESPN missing home/away designation"
        )
    return home, away


def _team_name(competitor: dict) -> str:
    team = competitor.get("team")
    if not isinstance(team, dict):
        return ""
    # ESPN uses `displayName` as the canonical full name (e.g. "Argentina").
    return _clean(team.get("displayName", ""), 80)


def _team_score(competitor: dict) -> int:
    """Score is exposed as a string in ESPN's summary; coerce to int.

    Raises EXTERNAL if the score is missing or non-numeric — that means the
    match isn't actually scored yet and we shouldn't be in this branch.
    """
    raw = competitor.get("score")
    if raw is None:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} ESPN missing score field")
    try:
        return int(str(raw))
    except (ValueError, TypeError):
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} ESPN score not integer: {raw}")


def _outcome_from_perspective(
    home_name: str,
    away_name: str,
    home_score: int,
    away_score: int,
    team_a: str,
    team_b: str,
) -> str:
    """Map (home, away, scores) → enum FROM THE PERSPECTIVE OF (team_a, team_b).

    The contract is parameterized by team_a / team_b, which may match
    either home or away. We do a case-insensitive substring match so
    "Argentina" matches "Argentina" even if ESPN formats it slightly
    differently (e.g. "ARGENTINA").
    """
    if home_score == away_score:
        return "DRAW"

    home_lc = home_name.lower()
    away_lc = away_name.lower()
    a_lc = team_a.lower()
    b_lc = team_b.lower()

    a_is_home = (a_lc in home_lc) or (home_lc in a_lc)
    a_is_away = (a_lc in away_lc) or (away_lc in a_lc)
    b_is_home = (b_lc in home_lc) or (home_lc in b_lc)
    b_is_away = (b_lc in away_lc) or (away_lc in b_lc)

    # If we can't unambiguously place team_a on one side, refuse — the
    # constructor args don't match the event. EXPECTED so leader+validator
    # agree byte-equal.
    if a_is_home and not a_is_away and b_is_away and not b_is_home:
        winner_is_a = home_score > away_score
    elif a_is_away and not a_is_home and b_is_home and not b_is_away:
        winner_is_a = away_score > home_score
    else:
        raise gl.vm.UserError(
            f"{ERROR_EXPECTED} team names do not match ESPN event sides"
        )

    return "TEAM_A_WIN" if winner_is_a else "TEAM_B_WIN"


def _derive_outcome(team_a: str, team_b: str, espn_event_id: str):
    """The SAME pipeline both leader and validator run.

    Returns (outcome, score, home_away_csv).
    Raises gl.vm.UserError with a 4-prefix-tagged message on failure.
    """
    event_id = _validate_event_id(espn_event_id)
    url = ESPN_SUMMARY_URL.format(event_id=event_id)
    data = _http_get_json(url)
    comp = _extract_competition(data)

    if not _is_final(comp):
        # EXPECTED so leader+validator agree byte-equal on "not yet final".
        raise gl.vm.UserError(
            f"{ERROR_EXPECTED} match not yet final"
        )

    home, away = _extract_competitors(comp)
    home_name = _team_name(home)
    away_name = _team_name(away)
    home_score = _team_score(home)
    away_score = _team_score(away)

    outcome = _outcome_from_perspective(
        home_name, away_name, home_score, away_score, team_a, team_b
    )
    score = f"{home_score}-{away_score}"
    home_away_csv = f"home={home_name},away={away_name}"
    return outcome, score, home_away_csv


class WorldcupEnumV4(gl.Contract):
    team_a: str
    team_b: str
    espn_event_id: str
    outcome: str        # primitive enum string
    score: str          # advisory primitive (home-away order)
    home_away_csv: str  # advisory primitive
    resolved: bool

    def __init__(self, team_a: str, team_b: str, espn_event_id: str):
        self.team_a = _clean(team_a, 80)
        self.team_b = _clean(team_b, 80)
        self.espn_event_id = _clean(espn_event_id, 16)
        self.outcome = "UNKNOWN"
        self.score = ""
        self.home_away_csv = ""
        self.resolved = False

    @gl.public.write
    def resolve(self):
        if self.resolved:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} already resolved")

        team_a = self.team_a
        team_b = self.team_b
        espn_event_id = self.espn_event_id

        # Advisory cache: score + sides captured on the leader run only.
        # These are NOT in calldata — the primitive that travels consensus
        # is the outcome enum string. Persisted to storage after success.
        advisory = {"score": "", "home_away": ""}

        def leader_fn() -> str:
            outcome, score, home_away = _derive_outcome(
                team_a, team_b, espn_event_id
            )
            advisory["score"] = score
            advisory["home_away"] = home_away
            # PRIMITIVE return: outcome enum string only.
            return outcome

        def validator_reproduce_fn() -> str:
            # For error-class reconciliation, the validator independently
            # runs the SAME pipeline as the leader.
            outcome, _score, _home_away = _derive_outcome(
                team_a, team_b, espn_event_id
            )
            return outcome

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                # SKILL.md canonical: EXTERNAL/EXPECTED byte-equal,
                # TRANSIENT-both-sides agree, LLM_ERROR/unknown disagree.
                return _handle_leader_error(leaders_res, validator_reproduce_fn)

            leader_primitive = leaders_res.calldata
            if not isinstance(leader_primitive, str):
                return False
            if leader_primitive not in VALID_OUTCOMES_SET:
                return False

            # Independent derivation by validator using the SAME pipeline.
            try:
                my_outcome, _score, _home_away = _derive_outcome(
                    team_a, team_b, espn_event_id
                )
            except gl.vm.UserError:
                # Validator couldn't derive but leader did → disagree.
                return False

            # Agree iff the validator's independently-derived outcome
            # matches the leader's primitive.
            return my_outcome == leader_primitive

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        # `result` is the primitive enum string.
        if isinstance(result, str) and result in VALID_OUTCOMES_SET:
            self.outcome = result
        else:
            self.outcome = "UNKNOWN"
        self.score = advisory.get("score", "")
        self.home_away_csv = advisory.get("home_away", "")
        self.resolved = True

    @gl.public.view
    def get_outcome(self) -> dict:
        return {
            "team_a": self.team_a,
            "team_b": self.team_b,
            "espn_event_id": self.espn_event_id,
            "outcome": self.outcome,
            "score": self.score,
            "home_away_csv": self.home_away_csv,
            "resolved": self.resolved,
            "needs_manual_review": self.outcome == "UNKNOWN" and self.resolved,
        }
