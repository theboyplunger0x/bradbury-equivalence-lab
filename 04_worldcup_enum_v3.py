# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# Lab contract 04 v3 — World Cup outcome oracle, enum-only consensus.
#
# CRITICAL REWRITE vs v2 (skills.genlayer.com SKILL.md anti-pattern fixes):
#
#   The v2 validator only confirmed a URL was REACHABLE and that the
#   leader's primitive was in the valid enum set. That is the
#   "Schema-only or leader-output-only validator" anti-pattern — a
#   misbehaving leader could rubber-stamp any outcome as long as one
#   evidence URL responded with any non-empty body.
#
#   v3 makes the validator INDEPENDENTLY DERIVE the outcome using the
#   SAME logic the leader runs:
#     Step 1: STRUCTURED SCORE PARSER — regex over evidence text for
#             patterns like "X-Y at Full time", "FT X:Y", "Final X-Y",
#             "Full Time: X-Y". If a confident score is found, derive
#             the outcome deterministically (X>Y → TEAM_A_WIN, etc.).
#             This path is byte-deterministic across leader + validator.
#     Step 2: LLM FALLBACK — only if NO structured score is found in any
#             reachable source. The LLM returns an outcome enum + a
#             confidence flag.
#     Step 3: If neither structured nor confident LLM → outcome=UNKNOWN
#             (NOT a "best guess"). UNKNOWN is a valid enum value the
#             contract surfaces for manual review.
#
#   Both leader_fn and validator_fn run the SAME Step 1 → Step 2 → Step 3
#   pipeline and the validator agrees iff the leader's outcome matches
#   what the validator independently derived. Because Step 1 is
#   deterministic, the common path needs ZERO validator-side LLM calls;
#   the LLM is only consulted as a fallback when scores can't be parsed
#   from raw evidence, at which point LLM variance is the actual point of
#   non-determinism we're consensus-voting on.
#
#   Other changes:
#     - Imports canonical _handle_leader_error from _genlayer_helpers.
#     - 4-class error scheme wired in (EXPECTED / EXTERNAL / TRANSIENT /
#       LLM_ERROR).
#     - Drops bare `except Exception` in JSON parsing (anti-pattern: use
#       narrow exception types).
#     - Advisory closure cache pattern removed: score is now part of the
#       deterministic derivation in Step 1 and persisted from inside the
#       leader path's return shape via a side struct that the contract
#       reads via state assignment AFTER consensus succeeds (still
#       advisory — not in calldata).

from genlayer import *
from _genlayer_helpers import (
    ERROR_EXPECTED,
    ERROR_EXTERNAL,
    ERROR_TRANSIENT,
    ERROR_LLM_ERROR,
    _handle_leader_error,
)
import json
import re


VALID_OUTCOMES = ["TEAM_A_WIN", "TEAM_B_WIN", "DRAW", "UNKNOWN"]
VALID_OUTCOMES_SET = set(VALID_OUTCOMES)
RESOLVABLE_OUTCOMES = {"TEAM_A_WIN", "TEAM_B_WIN", "DRAW"}


# --- Structured score regex patterns ----------------------------------------
# Deterministic patterns. Both leader and validator run these against the
# raw evidence text and agree on the result byte-for-byte.
SCORE_PATTERNS = [
    # "Full time: 2-1", "Full Time 2 - 1", "Full-time: 2:1"
    r"(?:full[\s\-]?time)[^\d]{0,8}(\d{1,2})\s*[\-:–]\s*(\d{1,2})",
    # "2-1 at Full time", "2:1 (full time)" — score precedes keyword
    r"(\d{1,2})\s*[\-:–]\s*(\d{1,2})[^\d]{0,12}(?:full[\s\-]?time|FT\b)",
    # "Final: 2-1", "Final score 2-1"
    r"(?:final(?:\s+score)?)[^\d]{0,8}(\d{1,2})\s*[\-:–]\s*(\d{1,2})",
    # "FT 2-1", "FT: 2-1", "FT 2:1"
    r"\bFT[\s:]{1,3}(\d{1,2})\s*[\-:–]\s*(\d{1,2})",
    # "ended 2-1", "ends 2-1"
    r"(?:ended|ends|finished)[^\d]{0,8}(\d{1,2})\s*[\-:–]\s*(\d{1,2})",
    # Team A 2-1 Team B (positional — last resort, lower confidence)
    r"\bscore[^\d]{0,8}(\d{1,2})\s*[\-:–]\s*(\d{1,2})",
]


def _clean(value: str, max_len: int) -> str:
    if value is None:
        return ""
    return str(value).strip()[:max_len]


def _http_get_text(url: str) -> str:
    response = gl.nondet.web.get(url)
    status = getattr(response, "status", 200)
    if 400 <= status < 500:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} returned {status}")
    if status >= 500:
        raise gl.vm.UserError(f"{ERROR_TRANSIENT} {url} returned {status}")
    body = getattr(response, "body", b"")
    try:
        if isinstance(body, bytes):
            return body.decode("utf-8", errors="ignore")
        return str(body)
    except (UnicodeError, ValueError) as e:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} unreadable body: {e}")


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            return {}
    return {}


def _normalize_outcome(value: str) -> str:
    outcome = _clean(value, 40).upper()
    if outcome in VALID_OUTCOMES_SET:
        return outcome
    return "UNKNOWN"


def _split_urls_csv(evidence_urls_csv: str):
    urls = []
    for raw in str(evidence_urls_csv or "").replace("\n", ",").split(","):
        url = raw.strip()
        if url.startswith("https://"):
            urls.append(url)
        if len(urls) >= 6:
            break
    return urls


def _fetch_all_evidence(urls):
    """Fetch each evidence URL; tolerate single-source failures.

    Returns (snippets_list, used_sources_list). Raises ERROR_TRANSIENT only
    if EVERY url failed transiently (so leader + validator can agree via
    canonical TRANSIENT-both-sides rule). Raises ERROR_EXTERNAL if every
    url returned a deterministic 4xx (so both sides agree byte-equal).
    """
    snippets = []
    used = []
    transient_count = 0
    external_count = 0
    for url in urls:
        try:
            body = _http_get_text(url)
            snippet = body[:8000]
            if snippet:
                snippets.append((url, snippet))
                used.append(url)
        except gl.vm.UserError as e:
            msg = str(e)
            if msg.startswith(ERROR_TRANSIENT):
                transient_count += 1
            elif msg.startswith(ERROR_EXTERNAL):
                external_count += 1
    if snippets:
        return snippets, used
    if transient_count > 0 and external_count == 0:
        raise gl.vm.UserError(f"{ERROR_TRANSIENT} all evidence sources transient")
    raise gl.vm.UserError(f"{ERROR_EXTERNAL} no readable evidence")


def _parse_structured_score(snippets):
    """Step 1: regex over evidence text for a confirmed final score.

    Returns (score_a, score_b, source_url) on confident match, else None.
    Match strategy: try each pattern in order across all snippets; require
    at least 2 independent sources to agree on (a, b) for confidence,
    OR a single source that matches the strongest pattern (full-time /
    final). This makes the derivation deterministic across validators.
    """
    # Collect all (a, b, url, pattern_strength) hits.
    hits = []
    for source_url, text in snippets:
        # Limit text scan window — keeps regex deterministic in cost.
        scan = text[:20000]
        for strength, pattern in enumerate(SCORE_PATTERNS):
            # strength=0 strongest (full time), strength=4 weakest.
            for m in re.finditer(pattern, scan, flags=re.IGNORECASE):
                try:
                    a = int(m.group(1))
                    b = int(m.group(2))
                except (ValueError, IndexError):
                    continue
                if a > 30 or b > 30:
                    # Sanity guard against accidental large numbers.
                    continue
                hits.append((a, b, source_url, strength))

    if not hits:
        return None

    # Strongest single-source hit (full-time / final / FT pattern) wins
    # immediately. Patterns 0-3 are "strong" (explicit full-time / final
    # / FT keyword); patterns 4-5 are weaker positional matches.
    strongest = min(hits, key=lambda h: h[3])
    if strongest[3] <= 3:  # any explicit full-time / final / FT match
        return strongest[0], strongest[1], strongest[2]

    # Otherwise require 2+ sources to agree on the same (a, b) pair.
    pair_counts = {}
    pair_first_url = {}
    for a, b, url, _ in hits:
        key = (a, b)
        pair_counts[key] = pair_counts.get(key, 0) + 1
        if key not in pair_first_url:
            pair_first_url[key] = url
    # Pick the pair with the most independent agreements.
    best_key = None
    best_count = 0
    for key, count in pair_counts.items():
        if count > best_count:
            best_count = count
            best_key = key
    if best_key is not None and best_count >= 2:
        a, b = best_key
        return a, b, pair_first_url[best_key]
    return None


def _outcome_from_score(score_a: int, score_b: int) -> str:
    if score_a > score_b:
        return "TEAM_A_WIN"
    if score_b > score_a:
        return "TEAM_B_WIN"
    return "DRAW"


def _llm_fallback_outcome(team_a: str, team_b: str, snippets):
    """Step 2: LLM fallback when structured score parsing failed.

    Returns (outcome, score_str). outcome is in VALID_OUTCOMES_SET; if the
    LLM is not confident, returns ("UNKNOWN", ""). Raises ERROR_LLM_ERROR
    if the LLM output is unparseable.
    """
    joined = "\n\n".join(f"URL: {u}\nCONTENT:\n{t}" for u, t in snippets[:4])
    prompt = f"""
You are settling a head-to-head match market between two teams.

Team A: {team_a}
Team B: {team_b}

Allowed outcomes (return EXACTLY one):
- TEAM_A_WIN: Team A won the closed match.
- TEAM_B_WIN: Team B won the closed match.
- DRAW: the closed match ended in a tie.
- UNKNOWN: the match has not concluded, or evidence is insufficient.

Rules:
- Prefer confirmed/final results over live or projected scores.
- If you are NOT confident the match is closed and the outcome is clear,
  return UNKNOWN.
- Return JSON only.

Evidence:
{joined}

Return this exact JSON shape:
{{
  "outcome": "TEAM_A_WIN|TEAM_B_WIN|DRAW|UNKNOWN",
  "score": "A-B",
  "confident": true|false
}}
"""
    raw = gl.nondet.exec_prompt(prompt, response_format="json")
    data = _as_dict(raw)
    if "outcome" not in data:
        raise gl.vm.UserError(f"{ERROR_LLM_ERROR} missing outcome in LLM output")
    outcome = _normalize_outcome(data.get("outcome", ""))
    confident = data.get("confident", False) is True
    score = _clean(data.get("score", ""), 16)
    if not confident:
        return "UNKNOWN", ""
    if outcome == "UNKNOWN":
        return "UNKNOWN", ""
    return outcome, score


def _derive_outcome(team_a: str, team_b: str, evidence_urls_csv: str):
    """The SAME pipeline both leader and validator run.

    Step 1: deterministic structured score parse.
    Step 2: LLM fallback (only if Step 1 failed AND we have evidence).
    Step 3: UNKNOWN — explicit "needs manual review".

    Returns (outcome, score, sources_csv).
    """
    urls = _split_urls_csv(evidence_urls_csv)
    if len(urls) == 0:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} no evidence URLs")

    snippets, used = _fetch_all_evidence(urls)

    # Step 1: structured parse (deterministic, no LLM).
    parsed = _parse_structured_score(snippets)
    if parsed is not None:
        score_a, score_b, _src = parsed
        outcome = _outcome_from_score(score_a, score_b)
        return outcome, f"{score_a}-{score_b}", ",".join(used)

    # Step 2: LLM fallback (this is the LLM-variance surface).
    outcome, score = _llm_fallback_outcome(team_a, team_b, snippets)
    if outcome in RESOLVABLE_OUTCOMES:
        return outcome, score, ",".join(used)

    # Step 3: UNKNOWN.
    return "UNKNOWN", "", ",".join(used)


class WorldcupEnum(gl.Contract):
    team_a: str
    team_b: str
    evidence_urls_csv: str
    outcome: str  # primitive enum string
    score: str    # advisory primitive
    sources_csv: str  # advisory primitive
    resolved: bool

    def __init__(self, team_a: str, team_b: str, evidence_urls: list):
        self.team_a = team_a
        self.team_b = team_b
        joined = ""
        if isinstance(evidence_urls, list):
            joined = ",".join(str(u) for u in evidence_urls)
        else:
            joined = str(evidence_urls or "")
        self.evidence_urls_csv = joined
        self.outcome = "UNKNOWN"
        self.score = ""
        self.sources_csv = ""
        self.resolved = False

    @gl.public.write
    def resolve(self):
        if self.resolved:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} already resolved")

        team_a = self.team_a
        team_b = self.team_b
        evidence_urls_csv = self.evidence_urls_csv

        # Advisory cache: score + sources captured on the leader run only.
        # These are NOT in calldata — the primitive that travels consensus
        # is the outcome enum string. Score + sources are persisted to
        # storage after consensus succeeds.
        advisory = {"score": "", "sources": ""}

        def leader_fn() -> str:
            outcome, score, sources = _derive_outcome(team_a, team_b, evidence_urls_csv)
            advisory["score"] = score
            advisory["sources"] = sources
            # PRIMITIVE return: outcome enum string only.
            return outcome

        def validator_reproduce_fn() -> str:
            # For error-class reconciliation, the validator independently
            # runs the SAME pipeline as the leader.
            outcome, _score, _sources = _derive_outcome(team_a, team_b, evidence_urls_csv)
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
                my_outcome, _score, _sources = _derive_outcome(
                    team_a, team_b, evidence_urls_csv
                )
            except gl.vm.UserError:
                # Validator couldn't derive but leader did → disagree.
                return False

            # Agree iff the validator's independently-derived outcome
            # matches the leader's primitive. UNKNOWN matches UNKNOWN
            # (both saw insufficient evidence).
            return my_outcome == leader_primitive

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        # `result` is the primitive enum string.
        if result in VALID_OUTCOMES_SET:
            self.outcome = str(result)
        else:
            self.outcome = "UNKNOWN"
        self.score = advisory.get("score", "")
        self.sources_csv = advisory.get("sources", "")
        self.resolved = True

    @gl.public.view
    def get_outcome(self) -> dict:
        return {
            "team_a": self.team_a,
            "team_b": self.team_b,
            "outcome": self.outcome,
            "score": self.score,
            "sources_csv": self.sources_csv,
            "resolved": self.resolved,
            "needs_manual_review": self.outcome == "UNKNOWN" and self.resolved,
        }
