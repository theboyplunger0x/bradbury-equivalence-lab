# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# Lab contract 04 — World Cup outcome oracle, enum-only consensus.
#
# Hypothesis: when the validator compares ONLY an enum field (outcome ∈
# {TEAM_A_WIN, TEAM_B_WIN, DRAW}) and ignores score + reasoning text, the
# LLM-driven oracle reaches consensus reliably even when each validator's
# LLM phrases its reasoning differently. The score and evidence strings are
# advisory only — surfaced in storage for humans, NOT used in the consensus
# vote.
#
# Path: leader fetches all evidence URLs, builds a prompt summarizing the
# fixture + evidence body excerpts, calls the LLM, parses {outcome, score}.
# Validators repeat the full leader path and compare only `outcome`.
#
# Notes:
# - This is a slimmed-down sibling of the production worldcup_outcome_oracle.
#   No structured-score gate, no source whitelist, no team-name aliasing —
#   the goal is to isolate the enum-only consensus pattern.
# - All gl.nondet.* calls happen inside leader_fn / validator_fn.

from genlayer import *
import json


ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"

VALID_OUTCOMES = ["TEAM_A_WIN", "TEAM_B_WIN", "DRAW"]


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
    if outcome in VALID_OUTCOMES:
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


def _derive_outcome(team_a: str, team_b: str, evidence_urls_csv: str) -> dict:
    urls = _split_urls_csv(evidence_urls_csv)
    if len(urls) == 0:
        raise gl.vm.UserError(f"{ERROR_EXTERNAL} no evidence URLs")

    snippets = []
    used_sources = []
    for url in urls:
        try:
            body = _http_get_text(url)
            # Truncate to keep prompt size reasonable.
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

    return {
        "team_a": _clean(team_a, 40),
        "team_b": _clean(team_b, 40),
        "outcome": outcome,
        "score": score,
        "sources": ",".join(used_sources),
    }


class WorldcupEnum(gl.Contract):
    team_a: str
    team_b: str
    evidence_urls_csv: str
    outcome: str
    score: str
    sources_csv: str
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

        def leader_fn() -> dict:
            return _derive_outcome(self.team_a, self.team_b, self.evidence_urls_csv)

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                try:
                    _derive_outcome(self.team_a, self.team_b, self.evidence_urls_csv)
                    return False
                except gl.vm.UserError as e:
                    msg = str(e)
                    lmsg = getattr(leaders_res, "message", "")
                    # Transient upstream outages don't have to byte-match —
                    # any TRANSIENT on both sides is good enough.
                    if msg.startswith(ERROR_TRANSIENT) and lmsg.startswith(ERROR_TRANSIENT):
                        return True
                    return msg == lmsg

            mine = _derive_outcome(self.team_a, self.team_b, self.evidence_urls_csv)
            # Consensus on outcome enum ONLY. Score + sources are advisory.
            return mine["outcome"] == leaders_res.calldata["outcome"]

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        self.outcome = result["outcome"]
        self.score = result["score"]
        self.sources_csv = result["sources"]
        self.resolved = True

    @gl.public.view
    def get_outcome(self) -> dict:
        return {
            "team_a": self.team_a,
            "team_b": self.team_b,
            "outcome": self.outcome,
            "score": self.score,
            "resolved": self.resolved,
        }
