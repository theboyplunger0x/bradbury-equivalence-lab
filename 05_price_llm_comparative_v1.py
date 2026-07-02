# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# Lab contract 05 v1 — price oracle testing the canonical
# `gl.eq_principle.prompt_comparative` pattern from our production escrow.
#
# What this contract is for:
#   Our production escrow oracle uses the
#   `gl.eq_principle.prompt_comparative(fetch_and_parse, principle=...)` LLM
#   pattern to resolve exit price. We have NOT yet verified whether that
#   pattern actually converges under bradbury's 5-validator consensus. This
#   contract is the MINIMAL reproducer to test it in isolation — same call
#   shape as prod, no escrow / no transfers / no business logic.
#
# Bradbury-compat differences vs the production escrow:
#   1. Runner header: PINNED content-addressed hash (same as 02_v3 / 03_v3
#      / 04_v4). Prod uses `py-genlayer:latest`, which bradbury rejects with
#      "invalid runner id" (5/5 DISAGREE on bootstrap).
#   2. No transfers / no counterparties — this contract only stores the price
#      string. Isolating the LLM+prompt_comparative pattern from every other
#      moving part is the entire point.
#   3. `exit_price` stored as bare string (matches prod escrow's storage
#      shape) — no integer fixed-point, because the prompt_comparative
#      principle "the price number must be exactly the same" already asks
#      the LLM path itself to converge on identical text. This is exactly
#      how prod uses it.
#
# SKILL.md checklist notes (per ~/.claude/skills/genlayer-contract-review):
#   - Runner PINNED (item 1) ✅
#   - Self-contained, no sibling imports (item 2) ✅
#   - Validator does NOT rubber-stamp — `prompt_comparative` runs the SAME
#     `fetch_and_parse` on each validator and compares under the principle,
#     which IS the SKILL.md-sanctioned independent-derivation path for
#     LLM-in-leader flows (item 3) ✅
#   - Source: DexScreener JSON — proven consensus-friendly in 02_v3 (item 4) ✅
#   - Calldata primitive: bare string (item 5) ✅
#   - Storage: string (matches prod). No bare-float math anywhere (item 6) ✅
#   - Error prefix scheme with all 4 classes wired (item 7) ✅
#   - No bare `except Exception` in consensus path (item 8) ✅
#   - No random / time / os / sys / subprocess (item 9) ✅

from genlayer import *


# Canonical 4-class error prefix scheme (per SKILL.md errorPrefixScheme).
ERROR_EXPECTED = "[EXPECTED]"    # Business-logic error from the contract (deterministic).
ERROR_EXTERNAL = "[EXTERNAL]"    # External API returned a deterministic 4xx (deterministic).
ERROR_TRANSIENT = "[TRANSIENT]"  # Network failure or external 5xx (non-deterministic).
ERROR_LLM_ERROR = "[LLM_ERROR]"  # LLM misbehavior / unparseable output (non-deterministic).


class PriceLlmComparative(gl.Contract):
    # Market config — mirrors the production escrow's storage of these two fields.
    symbol: str
    dex_url: str

    # State
    exit_price: str  # matches prod escrow storage shape; "0" until resolved
    resolved: bool

    def __init__(self, symbol: str, dex_url: str):
        self.symbol = symbol
        self.dex_url = dex_url
        self.exit_price = "0"
        self.resolved = False

    @gl.public.write
    def resolve(self):
        """Fetch price via LLM + eq_principle.prompt_comparative — the exact
        pattern used by our production escrow's `resolve()`.

        The leader and each validator INDEPENDENTLY run `fetch_and_parse`
        (web.get + LLM extract). `prompt_comparative` then adjudicates
        equivalence under the natural-language `principle`. Under
        SKILL.md's classification this is the sanctioned LLM-in-consensus
        path because validators re-derive rather than rubber-stamp.
        """
        if self.resolved:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} already resolved")

        # Bind locals so the closure below doesn't touch `self.*` at
        # execution time (keeps the nondet path fully self-contained per
        # SKILL.md item 2).
        symbol = self.symbol
        dex_url = self.dex_url

        def fetch_and_parse():
            # Mirror of the production escrow's fetch_and_parse — same shape,
            # same 2000-char cap, same "return ONLY the price number"
            # instruction, so this contract is a faithful isolation of the
            # prod LLM pattern for the bradbury consensus test.
            try:
                response = gl.nondet.web.get(dex_url)
            except gl.vm.UserError:
                # Preserve prefix if web.get itself raised one of our classes.
                raise
            except (ConnectionError, TimeoutError, OSError) as e:
                # Network-level failure — non-deterministic.
                raise gl.vm.UserError(f"{ERROR_TRANSIENT} web.get failed: {e}")

            status = getattr(response, "status", 200)
            if 400 <= status < 500:
                raise gl.vm.UserError(
                    f"{ERROR_EXTERNAL} {dex_url} returned {status}"
                )
            if status >= 500:
                raise gl.vm.UserError(
                    f"{ERROR_TRANSIENT} {dex_url} returned {status}"
                )

            body_raw = getattr(response, "body", b"")
            if isinstance(body_raw, bytes):
                try:
                    body = body_raw.decode("utf-8")
                except (UnicodeDecodeError, UnicodeError) as e:
                    raise gl.vm.UserError(
                        f"{ERROR_EXTERNAL} {dex_url} unreadable body: {e}"
                    )
            else:
                body = str(body_raw)

            prompt = (
                f"Find the priceUsd for {symbol} from this DexScreener "
                f"data: {body[:2000]}. Pick the pair with highest "
                f"liquidity. Return ONLY the price number."
            )

            try:
                llm_out = gl.nondet.exec_prompt(prompt)
            except gl.vm.UserError:
                raise
            except (ValueError, TypeError, RuntimeError) as e:
                raise gl.vm.UserError(f"{ERROR_LLM_ERROR} exec_prompt failed: {e}")

            if not isinstance(llm_out, str):
                raise gl.vm.UserError(
                    f"{ERROR_LLM_ERROR} exec_prompt returned non-string"
                )

            trimmed = llm_out.strip()
            if not trimmed:
                raise gl.vm.UserError(
                    f"{ERROR_LLM_ERROR} exec_prompt returned empty string"
                )

            # PRIMITIVE return: bare string (SKILL.md item 5). Matches
            # exactly the production escrow's fetch_and_parse return.
            return trimmed

        # CANONICAL PROD PATTERN — same call shape as our production escrow's
        # `resolve()` call to `gl.eq_principle.prompt_comparative`.
        price_str = gl.eq_principle.prompt_comparative(
            fetch_and_parse,
            principle="The price number must be exactly the same",
        )

        self.exit_price = str(price_str).strip()
        self.resolved = True

    @gl.public.view
    def get_price(self) -> str:
        # Bare string return — primitive (SKILL.md item 5). Matches the
        # calldata shape produced by resolve() so callers see the same
        # value they'd get from the prod escrow's `exit_price` field.
        return self.exit_price
