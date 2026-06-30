# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# Lab contract 04 v2 — World Cup outcome oracle, enum-only consensus.
#
# Codex-driven changes vs v1:
#   1. Already used gl.nondet.web.get() (no change there).
#   2. leader_fn returns a PRIMITIVE STRING (the outcome enum), not a dict.
#      The enum is the only thing validators actually validate.
#   3. No floats / no prices here, but the same integer-fixed-point principle
#      applies: storage holds only primitives, never floats.
#   4. Advisory fields (score, sources, team_a, team_b inside the consensus
#      return) DO NOT travel in the consensus return anymore. Only `outcome`.
#      Score + sources are recovered by the leader-side advisory cache (see
#      _last_leader_advisory) and persisted to storage AFTER consensus, but
#      they never gate the vote.
#   5. validator_fn does NOT re-call the LLM. It checks that the leader's
#      primitive is in the valid enum set. Optionally, it does an independent
#      lightweight fetch to confirm at least one evidence URL is reachable,
#      but the consensus check itself is the enum-set membership of the
#      leader's primitive. This contains LLM variance to the leader.
#
# Storage is primitives only. @gl.public.view formats them as a dict for
# end-user readability.

from genlayer import *
import json


ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"

VALID_OUTCOMES = ["TEAM_A_WIN", "TEAM_B_WIN", "DRAW"]
VALID_OUTCOMES_SET = set(VALID_OUTCOMES)


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
    except Exception as e:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} {url} unreadable body: {e}")


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
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


def _leader_derive_outcome(team_a: str, team_b: str, evidence_urls_csv: str):
    """Leader path: fetch evidence, call LLM, return (outcome, score, sources).

    Only `outcome` is part of the consensus primitive. Score + sources are
    returned to the caller for storage but do NOT travel in the validator
    return value.
    """
    urls = _split_urls_csv(evidence_urls_csv)
    if len(urls) == 0:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} no evidence URLs")

    snippets = []
    used_sources = []
    for url in urls:
        try:
            body = _http_get_text(url)
            snippet = body[:4000]
            if snippet:
                snippets.append(f"URL: {url}\nCONTENT:\n{snippet}")
                used_sources.append(url)
        except gl.vm.UserError:
            # One flaky source shouldn't kill the whole evaluation.
            pass

    if len(snippets) == 0:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} no readable evidence")

    prompt = f"""
You are settling a head-to-head match market between two teams.

Team A: {team_a}
Team B: {team_b}

Allowed outcomes (return EXACTLY one):
- TEAM_A_WIN: Team A won the closed match.
- TEAM_B_WIN: Team B won the closed match.
- DRAW: the closed match ended in a tie.

Rules:
- Prefer confirmed/final results over live or projected scores.
- Return JSON only.

Evidence:
{chr(10).join(snippets)}

Return this exact JSON shape:
{{
  "outcome": "TEAM_A_WIN|TEAM_B_WIN|DRAW",
  "score": "A-B"
}}
"""
    raw = gl.nondet.exec_prompt(prompt, response_format="json")
    data = _as_dict(raw)
    outcome = _normalize_outcome(data.get("outcome", ""))
    score = _clean(data.get("score", ""), 16)
    return outcome, score, ",".join(used_sources)


def _validator_lightweight_check(evidence_urls_csv: str) -> bool:
    """Cheap deterministic check: at least one evidence URL is reachable.

    No LLM call. No re-derivation. The actual consensus check is
    membership-in-enum on the leader's primitive — this helper just ensures
    the validator isn't rubber-stamping a leader that cited zero real
    sources from its own POV. Transient outages on any single URL don't
    fail the validator; we only need one reachable source.
    """
    urls = _split_urls_csv(evidence_urls_csv)
    if len(urls) == 0:
        return False
    for url in urls:
        try:
            body = _http_get_text(url)
            if body and len(body) > 0:
                return True
        except gl.vm.UserError:
            continue
    return False


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
        # Normalize list -> CSV so we have a single string field in storage.
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
            raise gl.vm.UserError(f"{ERROR_EXTERNAL} already resolved")

        # Capture advisory fields from the leader run via a closure-side cache.
        # Only the primitive `outcome` is returned for consensus; score +
        # sources are persisted to storage after consensus succeeds.
        advisory = {"score": "", "sources": ""}

        def leader_fn() -> str:
            outcome, score, sources = _leader_derive_outcome(
                self.team_a, self.team_b, self.evidence_urls_csv
            )
            advisory["score"] = score
            advisory["sources"] = sources
            # PRIMITIVE return: outcome enum string only.
            return outcome

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                # Error path: TRANSIENT on both sides = agree; otherwise
                # require byte-equal message. Do NOT re-call the LLM.
                lmsg = getattr(leaders_res, "message", "")
                # Cheap reachability probe; if it fails transiently and the
                # leader also failed transient, agree.
                try:
                    reachable = _validator_lightweight_check(self.evidence_urls_csv)
                except gl.vm.UserError as e:
                    msg = str(e)
                    if msg.startswith(ERROR_TRANSIENT) and lmsg.startswith(ERROR_TRANSIENT):
                        return True
                    return msg == lmsg
                # If validator could reach evidence but leader errored, no agree.
                if reachable:
                    return False
                # Validator also has nothing; treat as transient agreement only.
                if lmsg.startswith(ERROR_TRANSIENT):
                    return True
                return False

            # Success path: leader returned a primitive string.
            leader_primitive = leaders_res.calldata
            if not isinstance(leader_primitive, str):
                return False
            # Consensus check #1: must be a valid enum value.
            if leader_primitive not in VALID_OUTCOMES_SET:
                return False
            # Consensus check #2: at least one evidence URL is reachable
            # from this validator's POV (cheap deterministic sanity).
            return _validator_lightweight_check(self.evidence_urls_csv)

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        # `result` is the primitive enum string.
        self.outcome = str(result) if result in VALID_OUTCOMES_SET else "UNKNOWN"
        # Persist advisory fields captured on the leader run.
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
        }
