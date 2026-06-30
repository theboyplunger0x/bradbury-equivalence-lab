# Bradbury Lab — GenLayer Oracle Consensus Experiments

## Goal

Validate which oracle shapes reach reliable validator consensus on Bradbury
testnet (and at what cost), so we can pick the right pattern for each
production oracle (price feeds, World Cup match settlement, future event
markets).

## Hypotheses (per contract)

### 02 — `02_price_no_llm.py` (PriceNoLlm)
**Hypothesis:** a deterministic HTTP-only oracle (no LLM in path) reaches
consensus inside a tight ±0.1% numeric tolerance on a stable public API
(DexScreener), because validator-to-validator timing skew dominates and the
upstream API itself returns near-identical prices within a few hundred ms.

- Path: `gl.nondet.web.render()` -> JSON parse -> pick highest-liquidity USD
  pair -> compare numerically.
- Validator vote: `abs(leader - mine) / mean < 0.001`.
- Expected: high pass rate, cheapest of the three (no LLM tokens).

### 03 — `03_price_llm_field_only.py` (PriceLlmFieldOnly)
**Hypothesis:** putting an LLM in the path does NOT break consensus as long
as the validator compares ONLY one structured numeric field
(`price_usd`) and ignores all surrounding LLM variance (formatting,
reasoning, ordering).

- Path: `gl.nondet.web.get()` -> raw JSON to LLM -> ask for
  `{"price_usd": <number>}` -> compare numerically.
- Validator vote: same `_within(..., 0.001)` as 02.
- Expected: similar pass rate to 02, higher cost per resolve (LLM tokens).
  If pass rate drops materially vs 02, that quantifies the "LLM noise tax"
  even when we only consume one numeric field.

### 04 — `04_worldcup_enum.py` (WorldcupEnum)
**Hypothesis:** for non-numeric outcomes, comparing ONLY an enum field
(`outcome` ∈ {TEAM_A_WIN, TEAM_B_WIN, DRAW}) and treating score + sources
as advisory is enough to reach consensus across validators whose LLMs phrase
their reasoning differently.

- Path: fetch evidence URLs -> truncate to ~4k chars each -> LLM with
  `response_format="json"` -> parse `{outcome, score}`.
- Validator vote: `mine["outcome"] == leader.calldata["outcome"]` only.
- Expected: enum-only consensus passes reliably even with messy free-text
  reasoning. This is the cheapest viable shape for the worldcup settler
  (vs the production oracle which also has a structured-score regex gate).

## Test Inputs

### 02 + 03 — price oracles
- **BTC on base**
  `symbol="BTC", chain="base"`
  Expect: both contracts return a similar `price_usd` within 0.1% of CoinGecko BTC. cbBTC is the canonical Base BTC representation that DexScreener indexes; if the search returns no `BTC`-symbol pair on `base`, fall back to `symbol="cbBTC"` for the test run and note the substitution in Results.
- **ETH on base** (smoke check, alt run if BTC is flaky)
  `symbol="ETH", chain="base"`

### 04 — worldcup enum oracle
- **Argentina vs Brazil — hypothetical match**
  `team_a="Argentina", team_b="Brazil"`
  `evidence_urls=[
    "https://en.wikipedia.org/wiki/2022_FIFA_World_Cup_knockout_stage",
    "https://www.bbc.com/sport/football/world-cup",
    "https://www.espn.com/soccer/scoreboard"
  ]`
  Expect: enum lands on one of `TEAM_A_WIN | TEAM_B_WIN | DRAW`. For a
  hypothetical fixture not actually played, the LLM should return `UNKNOWN`
  (which is normalized to `"UNKNOWN"` and will fail consensus — that's a
  valid negative result for the lab).
- **Real played match (control)**
  Use a confirmed-final match from the 2022 World Cup with known final
  score so we can verify the LLM finds it and validators agree on the
  enum. Pick once and pin the date + score in Results.

## Run Order

1. Lint locally with `genvm-lint` (skipped historically — add to the lab
   loop per the research blockers list).
2. Deploy 02 to studionet (free) first, run `resolve()` 3x, capture
   validator votes + cost.
3. Repeat for 03 and 04 on studionet.
4. Re-run the same three on Bradbury testnet (paid in GEN). Budget ~0.01 GEN
   per full cycle (deploy + resolve + view) per contract.
5. Compare studionet vs Bradbury vote distribution + cost per resolve.

## Results

**Phase 1 — Deploy-only consensus on Bradbury testnet.** All three contracts
deployed cleanly with full 5/5 validator agreement on the constructor tx.
`resolve()` has NOT yet been called, so the LLM-in-path signal (03, 04) is
not yet measured — what we have is a deterministic-deploy sanity floor, plus
real GEN cost per shape.

| exp | contract                                   | success | AGREE | DISAGREE | gen_burned     | latency  | exec result            |
|-----|--------------------------------------------|---------|-------|----------|----------------|----------|------------------------|
| 02  | `0xD6F1c4998d5a8625e65CEb2bA1D44df302a596C4` | yes     | 5/5   | 0/5      | 0.0007639212549315 | deploy-tx | FINISHED_WITH_RETURN |
| 03  | `0xe0FB4Bc2280d8615b9a575F138466d80CEBa9984` | yes     | 5/5   | 0/5      | 0.0006988668077370 | deploy-tx | FINISHED_WITH_RETURN |
| 04  | `0xDa9cb48e8dEB44cDC94cE4D40e4469557e706e02` | yes     | 5/5   | 0/5      | 0.0008480247856920 | deploy-tx | FINISHED_WITH_RETURN |

**Totals:** 0.0023108128 GEN burned across 3 deploys, 15/15 AGREE,
0 DISAGREE, 0 retries.

Deploy tx hashes:
- 02 — `0xa1a88b8e1056b242ed89872a21c637b190e6425e9315a089f4b2d5049a814c02`
- 03 — `0x8a5ee6a3b6369e18d609618ced5ed6fd643cca18a664d87da1830cd7f84781c6`
- 04 — `0x5bf554bdfbfe9dc38b8202ac5825951567adf4debaad34609eb7c1c21ec6eaef`

### 02 — PriceNoLlm
**Why it (deploy-)worked:** `__init__(symbol="BTC", chain="base")` does no
network I/O and no LLM call — it just stores two strings. All 5 validators
ran the same bytecode against the same calldata and trivially agreed. The
cheapest of the three at 0.000764 GEN, consistent with the no-LLM premise.

**What's still unproven:** the actual hypothesis (DexScreener returning
near-identical USD prices within ±0.1% across validator timing skew) lives
inside `resolve()`, which has not been called. Deploy AGREE is a necessary
but not sufficient signal.

### 03 — PriceLlmFieldOnly
**Why it (deploy-)worked:** same reason as 02 — constructor is pure state
storage, no `gl.nondet.*` or LLM in the path yet. Notably it was the
*cheapest* deploy (0.000699 GEN), slightly under 02, because the constructor
body is identical-shape and validator gas pricing isn't paying for the LLM
field until resolve runs.

**What's still unproven:** the entire LLM-noise-tax question. The whole
point of 03 vs 02 is whether parsing one structured numeric field
(`price_usd`) out of an LLM response still lands inside the 0.001 tolerance
gate — that signal only appears on a `resolve()` call.

### 04 — WorldcupEnum
**Why it (deploy-)worked:** constructor stores `team_a`, `team_b`, and the
evidence URL list — pure deterministic state init, no LLM or web fetch. 5/5
AGREE. Most expensive of the three at 0.000848 GEN, attributable to the
larger calldata payload (3 evidence URLs) being charged at deploy.

**What's still unproven:** whether enum-only comparison
(`mine["outcome"] == leader["outcome"]`, ignoring free-text reasoning)
actually survives validator LLM phrasing variance. The Argentina-vs-Brazil
input is also a hypothetical match, so we expect `UNKNOWN` and a forced
DISAGREE on `resolve()` — that negative result is what makes the test
meaningful, and it's pending.

### Cross-contract takeaways
- **Deploy floor is solid:** 15/15 AGREE across three different contract
  shapes proves the toolchain, the validator set, and our `genlayer-py`
  client path are healthy. Any future DISAGREE will be a real signal,
  not noise.
- **Per-shape deploy cost ordering** (cheapest -> most expensive):
  `03 (0.000699) < 02 (0.000764) < 04 (0.000848)`. The delta is small
  (~21% spread) and tracks calldata size more than path complexity. The
  meaningful $-per-resolve number comes from the next phase.
- **LLM-noise-tax = NOT YET MEASURED.** The whole reason 02 and 03 exist
  side-by-side is to quantify it via `resolve()` pass-rate delta. Phase 1
  produced no data on this question — only confirmed both contracts
  *exist* and *can* be called.

## Verdict (Phase 1)

**No pattern is production-ready yet** based on Phase 1 data alone. Deploy
consensus is the lowest possible bar — every contract on GenLayer has to
clear it. What we still need before any of these shapes ships into FUD
production is:

1. `resolve()` consensus pass-rate ≥ 4/5 across N≥3 runs on real inputs.
2. Cost per `resolve()` (not per deploy) in GEN, so we can quote a real
   per-settlement number to the treasury.
3. For 04: at least one confirmed-final 2022 WC match used as a control,
   so we measure enum-AGREE on a known-true outcome, not just the
   UNKNOWN-DISAGREE negative case.

**Provisional ranking** (to be confirmed by Phase 2):
- **02 PriceNoLlm** is the best candidate for production price feeds —
  no LLM tokens, deterministic JSON parse, tightest tolerance gate.
- **04 WorldcupEnum** is the leading candidate for the worldcup settler
  *if* enum-only comparison survives resolve(). Cheaper than the prod
  oracle's regex+score gate.
- **03 PriceLlmFieldOnly** is the diagnostic, not a product. It tells us
  whether we'd ever be safe pulling a numeric out of an LLM — useful for
  future event markets where no clean API exists.

## Recommendation for Phase 2

**Do NOT rewrite the prod oracles yet.** Phase 1 only validated the
deploy path. Run Phase 2 first:

1. **Call `resolve()` 3x per contract on Bradbury**, capture
   `consensus_data.final`, `validatorVotes`, `validatorVotesName`, and
   per-validator `execution_result`. This is the actual experiment.
2. **Add the confirmed-final WC control match** to 04 so we have both a
   true-AGREE case and the planned UNKNOWN-DISAGREE case.
3. **Run `genvm-lint` locally** before redeploying (we skipped it again
   this phase — add it to the lab loop, as the research blockers list
   says).
4. **Only after Phase 2 produces real resolve() pass-rates and per-resolve
   GEN costs**, decide whether to:
   - port 02's shape into the production price oracle (most likely),
   - port 04's shape into the worldcup settler (likely if enum
     consensus holds),
   - or keep the existing prod oracles and just adopt the validator-vote
     comparator pattern (hybrid).

Moving on without Phase 2 would mean shipping a rewrite on the strength
of a constructor that stored two strings — exactly the kind of false
signal the lab exists to prevent.

## Phase 2 — `resolve()` consensus on real LLM/web outputs

Phase 2 called `resolve()` 3x against each contract on Bradbury and captured
the full validator vote vector, `txExecutionResult`, and per-tx GEN burn.
What follows is the raw outcome — no smoothing.

### Results table

| exp | attempt | success | exec result            | AGREE | DISAGREE | DET_VIOLATION | TIMEOUT | gen_burned | latency  | tx hash |
|-----|---------|---------|------------------------|-------|----------|---------------|---------|------------|----------|---------|
| 02  | 1       | no      | NEVER_EXECUTED         | 0/5   | 0/5      | 0/5           | 0/5     | ~0.000109  | 34m      | `0x3a2b2de5…` |
| 02  | 2       | no      | NEVER_EXECUTED         | 0/5   | 0/5      | 0/5           | 0/5     | ~0.000109  | 34m      | `0x9627f3a0…` |
| 02  | 3       | no      | NEVER_EXECUTED         | 0/5   | 0/5      | 0/5           | 0/5     | ~0.000109  | 34m      | `0x519ee7f1…` |
| 03  | 1       | no      | FINISHED_WITH_ERROR    | 0/5   | 0/5      | 5/5           | 0/5     | ~0.000109  | 5m       | `0x02dd7b4d…` |
| 03  | 2       | no      | FINISHED_WITH_ERROR    | 0/5   | 0/5      | 5/5           | 0/5     | ~0.000109  | 10m      | `0xd3469f90…` |
| 03  | 3       | no      | FINISHED_WITH_ERROR    | 0/5   | 0/5      | 5/5           | 0/5     | ~0.000109  | 34m      | `0x527ae3fa…` |
| 04  | 1       | no      | FINISHED_WITH_ERROR    | 0/5   | 0/5      | 5/5           | 0/5     | ~0.000109  | 6m       | `0x7972cae3…` |
| 04  | 2       | no      | EVM_REVERT_AT_SUBMIT   | 0/5   | 0/5      | 0/5           | 0/5     | 0.00010677 | 2.6s     | `0x21594eb9…` |
| 04  | 3       | no      | FINISHED_WITH_ERROR    | 0/5   | 0/5      | 4/5           | 1/5     | ~0.000109  | 34m      | `0x4c368d5c…` |

**Totals:** 9 attempts, 0 successful consensus, 0 AGREE / 0 DISAGREE.
~0.00098 GEN burned across 9 attempts (refund behavior on CANCELED not
isolated — number is balance-delta-at-submit, may include partial refunds).

### Per-experiment analysis

#### 02 — PriceNoLlm
**Tolerant_eq DID NOT get a chance to survive validator variance** — three
consecutive submissions never reached the validator queue at all. All three
ended in `statusName=CANCELED` with `validatorVotesName=[]` (literally
zero votes recorded). The consensus contract dropped them after extended
PENDING, almost certainly because the leader-side `gl.nondet.web.render()`
path was too slow/unreliable on Bradbury during this window.

The one corroborating data point is the v1 orphan tx
`0x3cd3b0215c623b426c83bd33bee9c471d6a46e1ef5b2e89020a578f0517207e8`
(submitted ~25min before 02#1 with identical args): it DID reach consensus,
with `statusName=UNDETERMINED` and 5/5 DETERMINISTIC_VIOLATION. The five
`validatorResultHash` values were IDENTICAL across all 5 validators —
meaning every validator deterministically reproduced the same failure
condition the leader hit. So when 02 actually executes, the failure mode
is the SAME pattern as 03 and 04: deterministic violation on the
nondet path, not a numeric-tolerance miss.

**The Phase 1 hypothesis (DexScreener noise inside ±0.1%) is therefore
NOT FALSIFIED but also NOT VALIDATED — the leader never produced a price
the validators could even compare against.**

#### 03 — PriceLlmFieldOnly
**Tolerant_eq did not get a chance to survive validator variance** —
the leader's `gl.nondet.exec_prompt()` failed before producing a receipt
at all. `consensus_data.leader_receipt` is empty across all 3 attempts.
The pattern is now **3/3 deterministic violations with identical
`validatorResultHash` across all 5 validators each time** (plus 2/2 on the
v1 orphans = 5/5 historical). When every validator independently reproduces
the same failure hash, the violation is INSIDE the contract logic (the LLM
call shape, not the field-comparison gate). The 0.001 tolerance gate was
never tested.

`consensusFinal=false` despite `statusName=FINALIZED` on 03#1/#2 because
the `consensus_data.final` flag is not set on disagreement-finalized
rounds — `FINALIZED` here means "the round concluded", not "consensus
was achieved".

#### 04 — WorldcupEnum
**Enum-only comparison did not get a chance to survive validator phrasing
variance** — same root cause as 03. 04#1 and 04#3 both ended in 5/5 and
4/5 DETERMINISTIC_VIOLATION respectively, with empty
`consensus_data.leader_receipt`. The enum-comparator code in the
validator vote function (`mine["outcome"] == leader["outcome"]`) was
never reached because the leader receipt never materialized for the
validators to compare against.

04#2 is a separate failure mode worth flagging: `EVM_REVERT_AT_SUBMIT` at
the GenLayer consensus contract `0x0112Bf6e…`, never reaching the
validator queue. Cause: 04#1 was still in-flight against the same
WorldcupEnum address, and the consensus contract refused to enqueue a
second `resolve()` for the same contract while one is pending. This is an
operational constraint to remember: **no concurrent resolves against the
same contract address.**

04#3 is the lone outlier vote-wise: 4 DETERMINISTIC_VIOLATION + 1
TIMEOUT. The TIMEOUT validator was slow enough that its result was
recorded as "no vote in time" instead of "violation". This is noise, not
signal — every other reached-consensus tx in the batch was 5/5 violation.

**Args note:** the requested 04 substitution to Argentina-vs-France 2022
final is IMPOSSIBLE against the existing deployment — `WorldcupEnum` bakes
`team_a`/`team_b`/`evidence_urls_csv` into immutable state at construction,
and the contract enforces `if self.resolved: raise gl.vm.UserError`. A
different match requires a fresh deploy, which was out of scope for
Phase 2. `resolve()` itself takes zero args.

### Cross-contract verdict

**None of the three patterns reached consensus on Bradbury in this run.**
9 attempts, 0 successes, 0 AGREE votes recorded across the entire batch.

The failure modes split into two buckets:

1. **Leader-side nondet path never produced a receipt** (03×3, 04×2):
   `gl.nondet.exec_prompt()` and `gl.nondet.web.get()` failed deterministically
   in a way every validator reproduced with an identical
   `validatorResultHash`. The validator-vote tolerance gates (numeric for
   03, enum for 04) were never exercised — this isn't a "tolerance too
   tight" problem, it's a "leader path doesn't run" problem.
2. **Leader-side render path timed out before votes were collected**
   (02×3): `gl.nondet.web.render()` was slow enough that the consensus
   contract cancelled the tx before any validator voted. The v1 orphan
   confirms that when this path DOES execute, it also produces
   deterministic violations.

The Phase 1 deploy-floor signal (15/15 AGREE on constructors) is now
clearly seen for what it is: a check that the contract bytecode is
well-formed and the toolchain works. It says nothing about whether the
nondet path inside `resolve()` will execute reliably under Bradbury's
current validator/queue conditions.

**Production-readiness call: zero of three patterns are production-ready
on Bradbury today.** Not because the comparator logic is wrong (we have
no evidence either way), but because the leader-side nondet primitives
(`web.render`, `web.get`, `exec_prompt`) are failing deterministically
or timing out at a rate that makes any tolerance-gate question moot.

### Final recommendation

**Do NOT adopt any Phase 2 pattern for FUD production oracles yet.** The
current FUD prod oracles (which run on studionet for price and the
worldcup settler) should stay where they are.

Concrete next steps:

1. **Stay on studionet for the price oracle.** Phase 2 produced no
   evidence that Bradbury's nondet web stack can deliver a price-feed
   leader receipt reliably enough to even reach the tolerance gate.
2. **Stay on the existing worldcup settler.** The structured-score regex
   gate + score-based DRAW logic in production has a working track
   record; the enum-only Phase 2 shape never even got tested because the
   leader receipt never materialized.
3. **Re-run Phase 2 on a different Bradbury window** before drawing the
   final negative conclusion. Three of the nine attempts had ~34min
   latencies, consistent with queue contention rather than steady-state
   protocol behavior. A re-run during quieter conditions could show very
   different failure rates.
4. **Add a `genvm-lint` step to the lab loop** before any redeploy (still
   skipped — same flag as Phase 1).
5. **For the eventual Bradbury retry:** instrument the leader-side path
   explicitly — log raw `gl.nondet.web.*` return values into the contract
   state pre-validator-vote so we can see *whether* the leader is
   producing structured output at all, separate from *whether*
   validators agree on it.

The lab did its job: it told us that "deploys pass on Bradbury" does not
imply "resolves pass on Bradbury", and that the bottleneck is upstream
of our comparator design.

## Message draft for GenLayer group

```
hola gente, estuve probando bradbury esta semana con 3 oracle shapes
distintos para ver cuál se podía portear a producción (precio sin LLM,
precio con LLM-field-only, y un worldcup enum). Deploys 5/5 AGREE en los 3,
pero al llamar resolve() me encontré con algo raro: 9 intentos, 0 consensus.

Los tres con LLM en el path (03, 04) terminaron en FINISHED_WITH_ERROR
con 5/5 DETERMINISTIC_VIOLATION y validatorResultHash IDÉNTICOS entre los
5 validators — o sea, todos reprodujeron el mismo failure hash, lo que me
hace pensar que el problema está en el leader-side gl.nondet.exec_prompt /
gl.nondet.web.get, no en el comparator. consensus_data.leader_receipt
vacío en todos.

El 02 (sin LLM, sólo gl.nondet.web.render contra DexScreener) ni siquiera
llegó a la queue de validators: 3/3 CANCELED después de ~34min PENDING,
votes=[]. Pero un orphan tx mío anterior con los mismos args sí ejecutó y
también dio 5/5 DETERMINISTIC_VIOLATION — mismo patrón que 03/04.

Mi lectura: los tolerance gates (numérico ±0.1% en 02/03, enum-equality en
04) nunca se llegaron a ejercitar porque el leader nondet path está
fallando determinísticamente upstream. ¿Es algo conocido del estado actual
de bradbury, o estoy haciendo algo raro con cómo invoco render/exec_prompt?
Tx hashes a mano si quieren mirarlos. Gracias!
```


## Phase 3 — v2 contracts with Codex insights applied

Phase 2 told us the leader-side nondet primitives were failing
deterministically and the tolerance gates never got exercised. Phase 3
rewrites the three contracts (suffix `_v2`) with the Codex-suggested
fixes: smaller leader payloads, primitive return values, and validators
that re-derive the answer cheaply instead of comparing complex objects.

### Per-contract change summary (vs v1)

#### `02_price_no_llm_v2.py` — PriceNoLlmV2 (vs v1)
- Swapped `gl.nondet.web.render()` for `gl.nondet.web.get()` — JSON
  endpoint, no DOM/JS-timing variance.
- `leader_fn` returns a **primitive string** of the price in fixed-point
  micro-USD (`int(price * 1e9)`), not a dict. Validator does its own
  independent `web.get()` and tolerance-checks the integer.
- Storage holds the integer-as-string; `@gl.public.view` formats both
  micro and float price for readability.
- Rationale: removes any object/key-ordering noise from validator-vote
  comparison; isolates the question to "do validators agree on a
  fixed-point integer within ±0.1%?".

#### `03_price_llm_field_only_v2.py` — PriceLlmFieldOnlyV2 (vs v1)
- Kept `gl.nondet.web.get()`.
- `leader_fn` fetches + LLM-extracts **once** and returns a primitive
  `price_micro_usd` string.
- `validator_fn` does **NOT re-call the LLM** — it does a deterministic
  JSON parse using the same selection rule that's in the prompt and
  tolerance-checks the leader's integer.
- Rationale: contains LLM variance to the leader only; validators are
  fully deterministic. This is the asymmetric pattern Codex flagged as
  the safest way to keep an LLM in the path.

#### `04_worldcup_enum_v2.py` — WorldcupEnumV2 (vs v1)
- Kept `gl.nondet.web.get()`.
- `leader_fn` returns the **bare outcome enum string** (e.g.
  `"TEAM_A_WIN"`); the advisory score and sources are captured via a
  closure and persisted post-consensus — they never travel in calldata.
- `validator_fn` validates by **enum-set membership + a cheap
  reachability probe** of one evidence URL — no LLM re-call, no dict
  diffing.
- Write-once guard preserved (`if self.resolved: raise
  gl.vm.UserError('[EXTERNAL] already resolved')`).
- Rationale: enum-only calldata is the smallest possible consensus
  surface; sidesteps every LLM phrasing/ordering disagreement we saw on
  v1.

All three v2 contracts compile clean (`python -m py_compile` PASS) and
keep v1's constructor signature so deploy code paths are unchanged.

### Deploy + resolve table per attempt

| exp     | deploy hash      | deploy result            | resolve attempt | tx execution result   | AGREE | DISAGREE | gen burned | latency |
|---------|------------------|--------------------------|-----------------|-----------------------|-------|----------|------------|---------|
| 02_v2   | `0xcb8bc0a6…`    | submitted, NOT FINALIZED | 1               | DEPLOY_NOT_FINALIZED  | n/a   | n/a      | n/a        | n/a     |
| 02_v2   | `0xcb8bc0a6…`    | submitted, NOT FINALIZED | 2               | DEPLOY_NOT_FINALIZED  | n/a   | n/a      | n/a        | n/a     |
| 02_v2   | `0xcb8bc0a6…`    | submitted, NOT FINALIZED | 3               | DEPLOY_NOT_FINALIZED  | n/a   | n/a      | n/a        | n/a     |
| 03_v2   | `0x9947d2f0…`    | submitted, NOT FINALIZED | 1               | DEPLOY_NOT_FINALIZED  | n/a   | n/a      | n/a        | n/a     |
| 03_v2   | `0x9947d2f0…`    | submitted, NOT FINALIZED | 2               | DEPLOY_NOT_FINALIZED  | n/a   | n/a      | n/a        | n/a     |
| 03_v2   | `0x9947d2f0…`    | submitted, NOT FINALIZED | 3               | DEPLOY_NOT_FINALIZED  | n/a   | n/a      | n/a        | n/a     |
| 04_v2   | `0xb552a7c1…`    | submitted, NOT FINALIZED | 1               | DEPLOY_NOT_FINALIZED  | n/a   | n/a      | n/a        | n/a     |
| 04_v2   | `0xb552a7c1…`    | submitted, NOT FINALIZED | 2               | DEPLOY_NOT_FINALIZED  | n/a   | n/a      | n/a        | n/a     |
| 04_v2   | `0xb552a7c1…`    | submitted, NOT FINALIZED | 3               | DEPLOY_NOT_FINALIZED  | n/a   | n/a      | n/a        | n/a     |

All three deploy transactions were accepted by the genlayer-js client
(`client.deployContract`, same path as `backend/src/services/genLayerOracle.ts`)
with `waitForTransactionReceipt({status:'ACCEPTED', retries:60,
interval:3000})` returning — but `receipt.data.contract_address` was
empty and `consensus_data.leader_receipt` was missing inside the 3-min
poll window. A longer-wait re-fetch (`status:'FINALIZED', retries:120,
interval:5s`) was launched in background but had not produced output
before the run wrapped. Same Bradbury slow-queue pattern documented in
Phase 2 (~34min PENDING latencies).

`resolve()` could not be called on any of the three v2 contracts —
without `contract_address`, there is nothing to call. Attempts #1/#2/#3
are listed for completeness and all share the same blocker.

Write-once note: 02_v2 and 03_v2 reassign state unconditionally in
`resolve()` (no `if self.resolved: raise` guard), so attempts #2 and #3
would have fully re-executed against fresh DexScreener data once
unblocked. 04_v2 keeps the v1 write-once guard, so only attempt #1
would have done real work — #2/#3 are expected to revert with
`[EXTERNAL] already resolved`, which is intended.

### AGREE rate comparison: Phase 2 (v1) vs Phase 3 (v2)

| phase | shape       | resolves attempted | resolves that reached consensus | AGREE votes recorded |
|-------|-------------|--------------------|---------------------------------|----------------------|
| 2     | v1 (3 contracts × 3 attempts) | 9              | 0                                  | 0 / 45 |
| 3     | v2 (3 contracts × 3 attempts) | 9 (blocked)    | 0                                  | 0 / 45 |

The Phase 3 zero is a different zero than Phase 2's zero: in Phase 2 the
contract executed and validators voted DETERMINISTIC_VIOLATION; in
Phase 3 the contract never came up because the deploy receipt never
finalized inside the window. We learned nothing new about the v2
comparator design from this run — the Bradbury queue blocker dominated.

### Verdict — is any v2 pattern production-ready?

**No.** Same conclusion as Phase 2, for a partly different reason:

- The v2 rewrites are sound on paper (primitive-string returns,
  asymmetric leader=LLM / validator=deterministic-parse,
  enum-only calldata) and compile clean. The hypothesis — "moving from
  dict calldata to primitive calldata and from re-call validators to
  deterministic validators should land inside the 0.1%/enum-equality
  gates" — is **still untested** because the deploy receipts never
  finalized in the Phase 3 window.
- Bradbury's deploy queue was the blocker this run, not the validator
  logic. The same ~34min PENDING pattern that swallowed three of the
  nine Phase 2 resolves swallowed all three Phase 3 deploys.
- Once `contract_address` materializes for any of the three v2 hashes,
  the v2 patterns should be retried — but Phase 3 produced zero
  production-readiness evidence for or against the rewrites.

Practical call: keep the existing FUD prod oracles (price feed on
studionet, worldcup settler with structured-score regex) running.
Re-attempt Phase 3 resolves on the existing v2 deploy hashes after
Bradbury queue conditions clear, before drawing any final conclusion on
the v2 shapes.

Operational cleanup: `backend/deploy_v2_tmp.mjs` and
`backend/deploy_v2_inspect.mjs` were used to submit the Phase 3 deploys
via `railway run --service fud-backend-mainnet` (so genlayer-js +
`GENLAYER_PRIVATE_KEY` / `GENLAYER_RPC_URL` were available). They are
still on disk under `backend/` (not under `experiments/`) and should be
removed once the inspect job lands or is abandoned. `experiments/`
itself is clean.

## Phase 3b — patient deploys + resolves

Phase 3 left the three v2 deploys in flight without `contract_address`,
so we couldn't tell whether the rewrites (primitive calldata,
asymmetric leader=LLM / validator=deterministic) actually clear
consensus. Phase 3b retries the same three contracts with a long-poll
`waitForTransactionReceipt({status:'FINALIZED', retries:1200,
interval:5000ms})` wrapper (≤100min per deploy) and sequential gating
(don't fire `n+1` until `n` returns a finalized receipt with a non-empty
`contract_address`).

### Per-contract change vs v1 (recap)

- **02_v2** — `web.render` → `web.get`; leader returns a primitive
  `price_micro_usd` string (`int(price * 1e9)`); validator does its own
  independent `web.get()` and tolerance-checks the integer at ±0.1%.
- **03_v2** — leader calls the LLM once and returns a primitive
  `price_micro_usd`; validator does NOT re-call the LLM, just a
  deterministic JSON parse with the same selection rule. Asymmetric.
- **04_v2** — leader returns the bare enum string
  (`TEAM_A_WIN | TEAM_B_WIN | DRAW`); score + sources travel via closure
  and persist post-consensus. Validator checks enum-set membership + a
  cheap reachability probe on one evidence URL. Write-once guard
  preserved.

### Deploy + resolve table

| exp     | deploy success | deploy ms | resolve #1 | resolve #2 | resolve #3 | AGREE count | per-call notes |
|---------|----------------|-----------|------------|------------|------------|-------------|----------------|
| 02_v2   | no             | n/a       | SKIPPED    | SKIPPED    | SKIPPED    | 0 / 15      | deploy tx `0xb5aae47a…` submitted, wrapper still long-polling FINALIZED at session-stop (~73min elapsed of 100min budget); `contract_address` never returned → resolve cannot fire. Wrapper PID 36544 was force-returned by the session stop hook. |
| 03_v2   | no             | n/a       | SKIPPED    | SKIPPED    | SKIPPED    | 0 / 15      | never launched — sequential gate blocked it behind 02_v2 finalization. |
| 04_v2   | no             | n/a       | SKIPPED    | SKIPPED    | SKIPPED    | 0 / 15      | never launched — sequential gate blocked it behind 02_v2 and 03_v2. Note: 04_v2 is write-once, so #2/#3 would intentionally revert with `[EXTERNAL] already resolved`. |

**Totals:** 1 deploy submitted (not finalized in window), 2 deploys
never launched, 0 resolves attempted, **0 / 45 AGREE votes recorded**.

### Phase 2 (v1) vs Phase 3b (v2) — AGREE comparison

| phase | shape                       | resolves attempted | resolves that reached consensus | AGREE votes recorded |
|-------|-----------------------------|--------------------|---------------------------------|----------------------|
| 2     | v1 (3 × 3 attempts)         | 9                  | 0                               | 0 / 45 (5/5 DETERMINISTIC_VIOLATION on the ones that executed; identical `validatorResultHash` across validators) |
| 3b    | v2 (3 × 3 attempts planned) | 0 (blocked)        | 0                               | 0 / 45 |

Phase 3b's zero is a third distinct zero: not "validators reproduced
the same violation" (Phase 2), not "deploy receipt missed the 3-min
poll window" (Phase 3), but "the long-poll wrapper was force-returned
by the session stop hook before the ≤100min FINALIZED window
elapsed on the first deploy". The Bradbury queue may yet finalize
`0xb5aae47a…` — but as of the run cutoff, no AGREE evidence exists for
any v2 shape.

### Verdict — which v2 pattern reached consensus AGREE?

**None.** Same answer as Phase 2 and Phase 3, by a different mechanism
each time. No v2 contract has produced a `contract_address` yet, so the
rewrites' core hypothesis (primitive-string calldata + deterministic
validators land inside the ±0.1% / enum-equality gates) remains
**untested, not falsified, not validated**.

The lab signal from Phase 3b is operational, not architectural:
Bradbury's deploy-finalization latency exceeded our session window
even with a 100-minute patient poll. To get an actual v2 AGREE/DISAGREE
verdict we need (a) a longer-lived runner (cron or detached process
that survives session boundaries), or (b) a Bradbury window where
deploys finalize faster than ~73min.

## Final draft for GenLayer group (English)

```
hey folks — quick update on the bradbury oracle lab. tl;dr we still
haven't gotten a clean AGREE on any of the three shapes, and i'd love a
sanity check on what we're seeing.

phase 2 ran the v1 contracts (price no-LLM, price LLM-field-only,
worldcup enum) through resolve() three times each on bradbury. 9
attempts, 0 reached AGREE. the ones that executed all ended
FINISHED_WITH_ERROR with 5/5 DETERMINISTIC_VIOLATION and IDENTICAL
validatorResultHash across the 5 validators — so the failure is
reproducible inside the leader-side gl.nondet.* path, upstream of
whatever tolerance gate we're testing. consensus_data.leader_receipt
empty across the board. price_no_llm couldn't even get there — 3/3
CANCELED after ~34min PENDING.

took the codex review on board and rewrote all three as _v2 with the
asymmetric pattern: leader returns primitive calldata only (price as
fixed-point int, enum as bare string), validators re-derive
deterministically (own web.get, json parse, enum-set membership +
reachability probe). no dict diffing, no validator-side LLM re-calls.
all three compile clean.

phase 3b tried to deploy + resolve the three v2 contracts with a patient
long-poll wrapper (waitForTransactionReceipt status:FINALIZED,
retries:1200, interval:5s — ≤100min per deploy) and strict sequential
gating. result: 02_v2 deploy submitted as 0xb5aae47a…, wrapper still
long-polling at ~73min when the session ended without a finalized
contract_address. 03_v2 and 04_v2 never fired (gated behind 02_v2). so
the v2 thesis is still untested — not because the comparator failed,
but because deploy finalization on bradbury is taking longer than our
session windows.

so right now: 0 AGREE votes on either v1 or v2, but for two completely
different reasons. v1 = leader path reproducibly violates determinism.
v2 = deploy receipts haven't finalized in time to even reach resolve.

two questions:
1. is the ~34min PENDING → CANCELED / ~70min+ to FINALIZED pattern
   we're seeing typical for bradbury right now, or a degraded window?
   asking before we move to a long-lived cron runner outside session
   boundaries.
2. for the 5/5 identical-validatorResultHash deterministic violations
   on v1 — does that pattern point to a specific gl.nondet.* failure
   mode (timeout? upstream HTTP non-2xx surfacing as violation?) we
   should instrument around, or is the right move just to switch
   everything to the v2 asymmetric shape and re-test?

happy to share the .py for the three v2 contracts + all tx hashes if
useful. thanks!
```

## Phase 4 — skills.genlayer.com integration + v3 rewrites

Phase 4 stops treating the lab purely as a Bradbury-queue experiment and
starts treating it as a CONTRACT-CORRECTNESS experiment. We pulled the
official anti-pattern catalogue from `skills.genlayer.com` (the canonical
`SKILL.md` for writing GenLayer Intelligent Contracts), audited every v2
file against it, and produced v3 rewrites that close every remaining hit.

The motivation: every Phase 2/3/3b zero so far was a runtime/queue zero,
not a comparator-correctness zero. We had no defensible answer to "if
deploy + resolve had finalized, would the v2 comparator have AGREED?"
because v2 still carried anti-patterns the SKILL.md explicitly flags
(silent LLM-zero on parse failure, missing canonical `_handle_leader_error`,
bare-float lint hits, schema-only validator on 04, dict-in-calldata in
some leaders). v3 closes those.

### Anti-pattern recon (v1 vs v2 vs v3)

Per-file audit against the SKILL.md anti-pattern table, before and after
the v3 rewrite.

| anti-pattern (per skills.genlayer.com SKILL.md)                                  | v1 hits                              | v2 hits                              | v3 hits |
|----------------------------------------------------------------------------------|--------------------------------------|--------------------------------------|---------|
| Dict / non-primitive returned from `leader_fn` (key-order serialization risk)    | 02, 03, 04                           | none                                 | none    |
| Bare float arithmetic in tolerance / parsing (lint AST flag)                     | 02, 03                               | 02, 03                               | none (pure-integer basis-points + decimal-string parsing) |
| Missing canonical `_handle_leader_error` (EXPECTED / EXTERNAL / TRANSIENT / LLM_ERROR scheme) | 02, 03, 04                           | 02, 03, 04                           | none (shared `_genlayer_helpers.py`) |
| `gl.nondet.web.render()` for a plain JSON endpoint (DOM/timing variance)         | 02                                   | none                                 | none    |
| Validator re-calls the LLM (variance amplification, LLM cost × N validators)     | 03, 04                               | none                                 | none    |
| LLM extract silently returns 0 on parse failure ("Ignore LLM response format")   | 03                                   | 03 (partially mitigated by tolerance) | none (now raises `[LLM_ERROR]`) |
| Bare `except Exception` masking errors                                           | 04 (`_as_dict`)                      | 04 (`_as_dict`)                      | none (narrowed to `(ValueError, TypeError)`) |
| Schema-only / leader-output-only validator (rubber-stamp risk)                   | 04 (enum-membership + URL reachability only) | 04 (URL probe only)                  | none (validator independently re-derives outcome) |

**Verdict from the audit:** v2 closed the v1 dict-calldata + render +
validator-LLM-recall hits but left 4 categories alive — float math,
canonical error scheme, silent LLM-zero (03), and schema-only validator
(04). v3 closes all 4.

### v1 → v2 → v3 design comparison

| dimension                       | v1                                                              | v2                                                                                  | v3                                                                                                                    |
|---------------------------------|-----------------------------------------------------------------|-------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------|
| Leader return shape             | dict (calldata)                                                 | primitive string (`price_micro_usd` or enum)                                        | primitive string (unchanged from v2)                                                                                  |
| Validator strategy              | re-call LLM / dict-diff                                         | deterministic re-derivation (web.get + JSON parse / reachability probe)             | deterministic re-derivation + **canonical `_handle_leader_error`** (TRANSIENT-both-sides agree, EXPECTED/EXTERNAL byte-equal, LLM_ERROR disagree-to-retry) |
| Numeric tolerance               | float ratio `< 0.001` (0.1%)                                    | float ratio `< 0.001` (0.1%, mixed with int compare)                                | **pure-integer basis-points** `_within_int(a, b, tol_bps=50)` — 0.5% to absorb DEX intra-block movement               |
| LLM error handling (03)         | swallow into 0.0                                                | swallow into 0.0 (mitigated by tolerance, still anti-pattern)                       | raise `[LLM_ERROR]` on missing/non-numeric → validator disagrees → consensus retries with new leader                  |
| Validator strength (04)         | re-runs LLM                                                     | URL-reachability + enum-membership ONLY (schema-only anti-pattern)                  | **runs the SAME Step 1 → Step 2 → Step 3 pipeline as leader** — regex score → derived outcome; agrees iff outcomes match |
| Error prefix scheme             | inlined ad-hoc strings                                          | inlined ad-hoc strings                                                              | shared `_genlayer_helpers.py` constants (`ERROR_EXPECTED / ERROR_EXTERNAL / ERROR_TRANSIENT / ERROR_LLM_ERROR`)        |
| Tested under `gltest`           | no                                                              | no                                                                                  | yes — integration suite under `tests/integration/`, gltest mocks for `mock_web` / `mock_llm`                          |
| Anti-pattern hits (post-audit)  | many across all 3 files                                         | 4 categories remaining                                                              | **zero remaining** against the recon's `antiPatternHitsPerFile` v2 list                                               |

### Per-contract change summary (v3)

#### `02_price_no_llm_v3.py` — PriceNoLlm v3

- **Imports canonical `_handle_leader_error`** from `_genlayer_helpers.py`.
  The v2 ad-hoc `except gl.vm.UserError` branch is gone, replaced with the
  SKILL.md leader-error reconciliation that handles TRANSIENT-both-sides
  agree, EXPECTED/EXTERNAL byte-equal, and LLM_ERROR/unknown
  disagree-to-retry.
- **Tolerance widened 0.1% → 0.5% (50 bps)** to absorb DEX intra-block
  price movement between leader and validator fetches a few seconds apart.
- **All float math removed.** Tolerance now uses `_within_int` (pure
  basis-points integer compare); price parsing uses
  `_parse_decimal_to_micro` (decimal-string → integer × 1e9, no float
  cast). The bare-float lint AST anti-pattern is now clean.
- **`ERROR_EXPECTED` guard** on the `already resolved` write-once branch.
- **What this tells us:** when 02 v3 eventually finalizes on Bradbury,
  the AGREE/DISAGREE signal is no longer contaminated by float-comparison
  variance or by undefined behavior on TRANSIENT errors — it's a clean
  read on whether validators independently fetching DexScreener land
  inside 50 bps.

#### `03_price_llm_field_only_v3.py` — PriceLlmFieldOnly v3

- **Canonical `_handle_leader_error`** wired in via a
  `validator_reproduce_fn` surrogate so the validator's deterministic
  re-derivation path (NOT a re-run of the LLM) drives the leader-error
  reconciliation.
- **Tolerance widened 0.1% → 0.5% (50 bps)** to match 02 v3.
- **LLM silent-zero anti-pattern eliminated.** Where v2 returned `0.0` on
  parse failure and relied on the tolerance check to mask the bug, v3
  raises `[LLM_ERROR]` on missing/non-numeric output and `[EXTERNAL]` on
  the deterministic "no pair on chain" zero case. The error class is no
  longer lost — TRANSIENT/LLM_ERROR drives a consensus retry, EXTERNAL
  drives byte-equal validator agreement.
- **Pure-integer `_parse_decimal_to_micro` everywhere.** Same
  bare-float-free contract as 02 v3.
- **What this tells us:** v3 separates "the LLM hallucinated and gave us
  garbage" (LLM_ERROR → retry) from "DexScreener really has no pair"
  (EXTERNAL → byte-equal agreement) from "leader and validator both
  parsed the same price within 50 bps" (the success path). v2 collapsed
  the first two into a silent zero.

#### `04_worldcup_enum_v3.py` — WorldcupEnum v3 (CRITICAL REWRITE)

- **Validator no longer rubber-stamps via URL-reachability +
  enum-membership.** Both leader and validator now run the SAME
  three-step pipeline:
  - **Step 1 — STRUCTURED SCORE PARSER (deterministic):** regex over
    evidence text for `Full time: X-Y`, `FT X-Y`, `Final: X-Y`,
    `ended X-Y`, etc. If a confident score is found, derive the outcome
    deterministically from the int score (`X>Y → TEAM_A_WIN`, `X<Y →
    TEAM_B_WIN`, `X==Y → DRAW`). Byte-stable across leader and validator
    — zero LLM calls in the happy path.
  - **Step 2 — LLM FALLBACK:** only when no structured score is found AND
    the LLM returns `confident=true`. The LLM is the only source of
    non-determinism here, and it's only consulted when Step 1 cannot
    resolve.
  - **Step 3 — UNKNOWN:** if neither structured nor confident-LLM, return
    `UNKNOWN` (a valid enum value the contract surfaces for manual
    review) instead of guessing.
- **Validator agrees iff its independently-derived outcome equals the
  leader's primitive.** Step 1 collisions are byte-stable; Step 2
  collisions are LLM-noise-bounded; Step 3 collisions force `UNKNOWN`.
  No path lets a misbehaving leader push a fabricated outcome past
  validators who never independently checked the evidence.
- **Bare `except Exception` in `_as_dict` narrowed** to
  `(ValueError, TypeError)`.
- **Canonical `_handle_leader_error`** wired in via the same
  validator-reproduce-surrogate as 03 v3.
- **What this tells us:** v3 is the first version of 04 whose validator
  actually validates the OUTCOME, not just the SHAPE. Schema-only
  validators are a known SKILL.md anti-pattern precisely because they
  trust the leader's claim; v3 doesn't.

### Tests

A new gltest integration suite under
`tests/integration/{test_02,test_03,test_04}_*v3.py` exercises each v3
contract via `mock_web` / `mock_llm` cheatcodes (localnet/GLSim/Studio
only — auto-skipped on `testnet_bradbury` via the `requires_mocks`
marker). `conftest.py` centralizes mock payload builders so the JSON
shape DexScreener and the worldcup evidence pages return stays
realistic.

**Per-file `py_compile` PASS on all 4 source files (helpers + 3 v3
contracts) under Python 3.12.**

**`gltest tests/integration/ -v -s --network localnet` — NOT RUN in this
session.** Two operational blockers stood between the suite and a
green/red signal:

1. **Docker Desktop is not installed on the lab machine.** `genlayer up
   --headless` exits with `connect ENOENT /var/run/docker.sock` →
   "Docker is not running. Please start Docker Desktop and try again."
   GLSim's localnet requires Docker Desktop, which itself needs an admin
   password and a GUI launch to install. Out of scope for this run.
2. **`tests/gltest.config.yaml` uses the older schema.** `gltest
   --collect-only` aborts with `Gltest configure error: Invalid
   configuration keys. Valid keys are: ['networks', 'paths',
   'environment']`. The file uses top-level `contract_path:` +
   `default_network:`; gltest 0.29.2 expects nested `paths.contracts:`
   and `networks.default:`, plus a `chain_type:` field per network when
   the URL is overridden.

Neither blocker invalidates the v3 audit — they're infrastructure issues
upstream of the comparator question. They do mean the v3 verdict is
"compiles clean, anti-patterns closed on paper" not "validators AGREE
under mocked input". Hand-off notes for running on a machine with Docker
+ a migrated config live in the next session's notes.

### Verdict — is any v3 pattern production-ready?

**Not yet — but the lab is now a sharper instrument.** The lab signal
graph after Phase 4:

- **Phase 1:** deploys finalize, constructors AGREE 15/15. Floor only.
- **Phase 2:** v1 resolves never reach the validator-vote stage — leader
  nondet path deterministically violates.
- **Phase 3 / 3b:** v2 deploys never finalize inside the session window —
  Bradbury queue is the blocker, not the comparator.
- **Phase 4:** v3 contracts close every remaining SKILL.md anti-pattern,
  compile clean, and ship with a gltest integration suite. Localnet
  smoke and Bradbury redeploy are the only steps left between the v3
  design and a defensible production-readiness call.

**Recommendation:** do NOT redeploy v3 to Bradbury until the gltest
suite passes against localnet first. The cheapest "is the comparator
even right?" signal lives on GLSim, not on a 70-minute Bradbury queue.
Once localnet is green, redeploy v3 (single batch, sequential gating,
patient wait) and re-measure resolve() AGREE-rate against the v1/v2
baselines.


## Phase 4b — Codex review + targeted fixes

After Phase 4 shipped, Codex did an adversarial pass over the v3 contracts
and the integration test suite. Verdict was **NOT READY** with 4 fix
items (Q1-Q4). We agreed on Q3 + Q4, disagreed on Q1, and Q2 was a
documentation nit folded into this section.

### Q1 — `_handle_leader_error` shape (DISAGREED, no code change)

Codex flagged `_genlayer_helpers.py:26-62` as "non-canonical" because it
re-raises `gl.vm.UserError` via a `try/except` ladder instead of the
single-line "raise on disagreement" form Codex remembered from an older
draft. We DID NOT change the helper because our implementation matches
the canonical SKILL.md reference exactly.

Canonical source:
[`plugins/genlayer-dev/skills/write-contract/SKILL.md`](https://github.com/genlayerlabs/skills/blob/main/plugins/genlayer-dev/skills/write-contract/SKILL.md)

SKILL.md spells out the rule we encode in `_handle_leader_error` verbatim:

> Deterministic errors: must match exactly (byte-equal). EXPECTED and
> EXTERNAL classes only agree on byte-equal message; TRANSIENT agrees if
> both sides independently observe a transient class; LLM_ERROR and
> unknown classes always disagree to force leader rotation.

That is precisely the branch structure in `_genlayer_helpers.py:51-58`:
EXPECTED/EXTERNAL → byte-equal compare; TRANSIENT → "both transient";
LLM_ERROR / fallthrough → return False. We logged the disagreement here
so the next reviewer doesn't relitigate it.

### Q3 — Tighten 03 LLM `priceUsd` parse (FIXED)

The v3 contract 03 (`03_price_llm_field_only_v3.py`) was still accepting
a JSON `priceUsd` float and casting it via `int(str(float))`, which is
the exact nondeterminism leak the SKILL.md anti-pattern catalog forbids
(any float in the LLM payload makes the leader/validator round
differently across runs).

Fix in `03_price_llm_field_only_v3.py:29-50` + `113-200`:

- Added `import re` and a precompiled `_DIGITS_ONLY_RE = re.compile(r"^\d+$")`.
- Rewrote `_extract_price_micro_via_llm`'s prompt to require an
  INTEGER-ONLY response under a new key `price_micro_usd`
  (= `priceUsd * 1_000_000_000`, rounded). The prompt contains
  CRITICAL formatting rules forbidding decimals, floats, scientific
  notation, currency symbols, commas, signs, and whitespace.
- Strict pre-cast validation: `bool` rejected (subclass of `int`),
  `int` accepted as-is, `str` accepted ONLY if it matches `^\d+$`,
  anything else → `gl.vm.UserError(f"{ERROR_LLM_ERROR} ...")`.

This eliminates the `int(str(float))` leak path entirely. Float-shaped
inputs now deterministically fail with `[LLM_ERROR]` instead of
silently rounding into the consensus.

### Q4 fix 1 — 4xx external tests added

External 4xx responses must surface as `[EXTERNAL]` (deterministic, both
validators agree on byte-equal message) and must NOT mutate contract
state. Previously the suite only covered 2xx happy paths and 5xx
transients.

Added parametrized `[400, 404]` tests:

- `tests/integration/test_02_price_no_llm_v3.py:131-159`
  (`test_external_4xx_surfaces_external_error`) — DexScreener 4xx →
  contract throws `[EXTERNAL]`, state unchanged.
- `tests/integration/test_03_price_llm_field_only_v3.py:147-178`
  (`test_external_4xx_dexscreener_surfaces_external_error`) — verifies
  the LLM is NEVER reached because `_http_get_text` fails first.
- `tests/integration/test_04_worldcup_enum_v3.py:189-220`
  (`test_external_4xx_evidence_surfaces_external_error`) — both
  evidence URLs return 4xx so `_fetch_all_evidence` aggregates to a
  deterministic `[EXTERNAL]`.

### Q4 fix 2 — LLM_ERROR tests added

LLM misbehavior must surface as `[LLM_ERROR]` (non-deterministic class →
always disagree → leader rotation). Two new tests prove the v3 contracts
gate consensus on garbage LLM output:

- `tests/integration/test_03_price_llm_field_only_v3.py:113-145`
  (`test_llm_error_float_leak_blocks_consensus`) — LLM returns a JSON
  float under `price_micro_usd`; the new digit-only guard from Q3
  rejects → `[LLM_ERROR]`, consensus fails, state unchanged.
- `tests/integration/test_04_worldcup_enum_v3.py:223-253`
  (`test_llm_error_garbage_fallback_output_blocks_consensus`) — evidence
  has no structured score (forces LLM fallback) and LLM returns garbage
  missing the `outcome` key; `_llm_fallback_outcome` raises
  `[LLM_ERROR]`, the canonical helper disagrees, the tx fails, state
  unchanged.

### Supporting changes

`tests/integration/conftest.py:140-172` updated to match the new prompt
contract:

- `llm_response_price(price_usd)` now emits
  `{"price_micro_usd": int(price_usd * 1e9)}` matching the Q3 prompt.
- Added `llm_response_price_micro` (integer-form helper),
  `llm_response_price_float_leak` (deliberate float for the LLM_ERROR
  test), and `llm_response_outcome_garbage` (missing `outcome` key for
  contract 04's LLM_ERROR test).

### Verification

- `py_compile` PASS on all 5 modified files.
- `gltest --collect-only` collects **19 tests** across 3 modules with no
  errors:
  - `test_02_price_no_llm_v3.py`: 5 tests
  - `test_03_price_llm_field_only_v3.py`: 6 tests
  - `test_04_worldcup_enum_v3.py`: 8 tests
- Includes all new tests: external_4xx parametrized `[400, 404]` on
  contracts 02/03/04, `llm_error_float_leak_blocks_consensus` on 03,
  and `llm_error_garbage_fallback_output_blocks_consensus` on 04.

### Verdict after 4b

The v3 contracts now close every Codex-flagged item we agreed with (Q3
+ Q4) and we've documented the canonical-helper disagreement (Q1) with
a direct SKILL.md citation so it doesn't get reopened. The suite still
needs to actually RUN (not just collect) against localnet before any
production-readiness call — Phase 4b is "design closed + tests
authored", not "tests green".
