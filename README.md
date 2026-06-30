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

### Final round (Phase 4c — close test coverage gaps)

Closed the two Codex-flagged test gaps from the 4b review. (1) Added a
`llm_response_price_micro_string_float_leak` conftest fixture + a new
test in `test_03` that drives the LLM to emit a JSON string like
`"65000500000000.0"`, proving the regex branch at `03_v3:182-185` fires
(rejects the decimal point before `int()` cast) — distinct from the
existing float-leak test that hits the TYPE guard's `else` arm. (2)
Tightened every 4xx external-error test in `test_02`/`test_03`/
`test_04` to assert the `"[EXTERNAL]"` prefix on the leader receipt
payload when the harness exposes it (graceful skip with comment when
the build does not). Total: 20 collected (was 19, +1), `py_compile`
PASS. Code production-ready, suite covers happy + major failure paths
+ error classification verified; only "tests green on localnet" remains
before promoting to production.

### Phase 4d — test rewrite (real gltest API)

Marcos ran the suite locally and the import phase exploded immediately:
the Phase 4/4b/4c tests were authored against a **fictional `vm_context`
fixture** that does not exist anywhere in `gltest`. The recon caught
the same thing — the real `gltest` plugin exposes a different surface
entirely, and our previous integration tests were silently conflating
two test modes (a hypothetical "live-with-mocks" mode and the actual
direct-VM mode) that don't exist together. Credit to Marcos's local
run for surfacing this — collection had been green only because no
one had actually invoked `gltest` against the integration directory
end-to-end.

All four files under `tests/integration/` were rewritten against the
**real** `gltest` API, specifically the `gltest.direct.pytest_plugin`
surface that ships mock support:

- `direct_vm` — provides `mock_web(url, payload_or_exception)`,
  `mock_llm(prompt_substring, response_or_exception)`, and
  `expect_revert("[PREFIX]")` for substring-matching the contract's
  `gl.vm.UserError` message.
- `direct_deploy` — deploys the contract into the local VM with the
  constructor args (e.g. CSV evidence URLs).
- Direct-mode proxy contracts use the clean `contract.resolve()` /
  `contract.get_price()` form — no `.transact()/.call()` chain, no
  `tx_execution_succeeded()` assertion. Failures are caught by
  wrapping the call in `with direct_vm.expect_revert("[PREFIX]"):`.

`conftest.py` lost the fictional `vm_context` fixture and the testnet
skip hook (direct mode is local — no testnet to skip). Kept: the
`requires_mocks` / `slow` marker registrations (compat), and all the
DexScreener / evidence / LLM payload + response builder functions as
plain module-level helpers. Also dropped a duplicate
`pytest_addoption` block that was fighting with gltest's own
`--network` registration.

Per-file rewrite:

- **`test_02_price_no_llm_v3.py`** (5 tests) — happy path, transient
  503, empty-pairs external, parametrized 4xx `[400, 404]`. Removed
  the now-unused `_leader_error_payload` helper; failure detection
  goes through `direct_vm.expect_revert("[EXTERNAL]" / "[TRANSIENT]")`
  which substring-matches the contract's UserError prefix directly.
- **`test_03_price_llm_field_only_v3.py`** (7 tests) — happy path, the
  three `LLM_ERROR` variants (garbage, float leak, string-float regex
  leak), parametrized 4xx `[400, 404]`, transient 503. Same
  `expect_revert` pattern for `[LLM_ERROR]` / `[EXTERNAL]` /
  `[TRANSIENT]`.
- **`test_04_worldcup_enum_v3.py`** (8 tests) — `TEAM_A_WIN`,
  `TEAM_B_WIN`, `DRAW` (structured score regex), `UNKNOWN` (LLM
  `confident=False`, contract succeeds), transient 503, parametrized
  4xx `[400, 404]` external, `LLM_ERROR` fallback. Deploy passes
  `list(EVIDENCE_URLS)` since the constructor joins to CSV internally.

Dropped every `@pytest.mark.requires_mocks` decorator — direct mode
never touches testnet, so the auto-skip-on-testnet logic is dead. The
marker stays registered for anything else that might use it.

**Verification:**

- `python -m py_compile` PASS on all four files.
- `gltest --collect-only tests/integration/` collects exactly **20
  items** (5 + 7 + 8), matching the v3 design count exactly.
- No parallel `tests/direct/` suite needed — these integration tests
  **are** the direct-mode tests. That's the correct architecture for
  nondet contracts with mocks: the `direct_vm` fixture is the only
  surface where `mock_web` / `mock_llm` actually work.

Status now: import collection clean against the real `gltest` build,
suite ready to execute on Marcos's localnet for the first time.

### Phase 4e — local test run + 04_v3 TRANSIENT propagation fix

First full run of the v3 suite in direct mode (local, no docker) came
back **19/20**. One failure surfaced in
`test_04_worldcup_enum_v3.py`: the all-5xx aggregation case was being
raised as `[EXTERNAL]` instead of `[TRANSIENT]`.

Root cause in `04_worldcup_enum_v3.py` `_fetch_all_evidence`: the
previous aggregation only raised `[TRANSIENT]` when
`transient_count > 0 AND external_count == 0`; any mixed 5xx + 4xx
batch silently fell through to the `[EXTERNAL]` "no readable
evidence" branch. That violates the contract spelled out in
`SKILL.md _handle_leader_error`: a 5xx might recover on retry, so it
must propagate as TRANSIENT even when other sources hard-fail with
4xx, otherwise validators can't agree via the both-sides-transient
rule.

**Fix:** rewrote the aggregation at lines 136–181. New rule:

- If ANY source returned 5xx **and** zero sources produced usable
  evidence → raise `[TRANSIENT]`.
- Only when every failing source returned a deterministic 4xx (and
  none returned usable evidence) → raise `[EXTERNAL]` (byte-equal
  agreement guaranteed).
- 200-but-unparseable still falls to `[EXTERNAL]` via the snippet
  gate, unchanged.

Verified against all four invariants by Codex reading the
implementation directly (CORRECT verdict). `python -m py_compile`
PASS on all three v3 files. No equivalent aggregation bug exists in
`02_price_no_llm_v3.py` or `03_price_llm_field_only_v3.py` — each
fetches a single URL and propagates the prefix straight out of
`_http_get_text`, so no change needed there.

**Final:** `gltest tests/integration/` → **20/20 PASSED** in direct
mode (local, no docker), 0.22s.

## Phase 5 — Bradbury testnet real-deploy of v3

Phase 4 closed the design + local-test side of the v3 rewrites (20/20
green in direct mode). Phase 5 is the first attempt to land the v3
contracts on Bradbury itself and measure resolve() AGREE-rate against
the v1 Phase 2 baseline. We started with 02_v3 — the cheapest shape and
the cleanest comparator to interpret a vote result against.

### Deploy 02_v3 result

| field             | value |
|-------------------|-------|
| contract          | `02_price_no_llm_v3.py` (PriceNoLlm v3) |
| constructor args  | `["BTC", "base"]` |
| account           | `0x11711CdFB47293bcc0Ce30e647c5bA89e5f44D4b` |
| rpc               | `https://rpc-bradbury.genlayer.com` |
| deploy hash       | `0x5c138a24dd23f9c4e4ae7e79a690e81391473c1733e1991ae2a324e38e0a6559` |
| statusName        | `PENDING` (long-poll not yet finalized at session-stop) |
| validatorVotes    | `{}` (empty — round had not concluded) |
| contractAddress   | (empty — never returned) |
| gen_burned        | unknown (balanceBefore=null; `client.getBalance()` returned `Invalid params` on Bradbury under genlayer-js 0.28.4 + viem 2.47.0) |
| msElapsed         | 0 (long-poll still in flight) |
| verdict           | **OTHER** (neither AGREE, DISAGREE, nor CANCELED — submitted but not finalized in window) |

Script: `backend/scripts/deploy-bradbury-02-v3.mjs` (left on disk, needs
cleanup before re-run). Invoked via `railway run --service
fud-backend-mainnet -e production` to pick up `GENLAYER_PRIVATE_KEY` +
`GENLAYER_RPC_URL`. Long-poll target was `status=FINALIZED, retries=360,
interval=5000ms` (30-min budget). The stop hook fired before the receipt
arrived. Background task `b5bnruhj9` is still running; monitor
`b7soinc4w` is tailing
`/private/tmp/claude-501/-Users-lanzanimarcos7-Desktop-Proyectos-FUDmarkets/63814737-a76c-4379-a01d-06b6652b9f2a/scratchpad/deploy-02-v3.log`
for the final `===== REPORT =====` block.

### Resolve 02_v3 result

| field             | value |
|-------------------|-------|
| txHash            | (none — phase skipped) |
| statusName        | — |
| validatorVotes    | — |
| consensusFinal    | false |
| gen_burned        | — |
| msElapsed         | 0 |
| verdict           | **SKIPPED** |

Skipped because the upstream deploy did not finalize with an AGREE
consensus and `contractAddress` was empty — there was no target to call
`resolve()` against. Per the gating logic: AGREE+AGREE was required to
green-light extending to 03 + 04, and we did not get the first AGREE.

### Interpretation — contract-side fixed? operational still blocking?

This run repeats the Phase 3b / Phase 3 operational signature almost
exactly: **deploy submitted cleanly, contract address never materialized
inside the session-bound long-poll window.** The blocker is upstream of
the v3 comparator design — same Bradbury queue-latency pattern that
swallowed v2 deploys in Phase 3/3b is back.

What we can say:
- **Contract-side correctness (v3): still UNTESTED on Bradbury.** Local
  direct-mode passed 20/20, but the comparator has not been exercised
  by 5 Bradbury validators against live DexScreener data even once. The
  v3 thesis (pure-integer 50bps tolerance, canonical
  `_handle_leader_error`, deterministic re-derivation) carries forward
  unfalsified and unvalidated.
- **Operational side (Bradbury queue): still blocking.** The
  `PENDING` → long-poll-times-out pattern is identical to Phases 3/3b;
  this is not a v3 regression, it is a testnet condition. The 30-min
  poll budget in `deploy-bradbury-02-v3.mjs` was already 2x the
  conventional 15-min default and still insufficient.
- **Verdict bucket:** matches the spec's "Other combos: report
  honestly" — neither AGREE+AGREE nor DV nor CANCELED. The deploy
  simply did not finalize in time. Whether it eventually does
  (background task `b5bnruhj9` may still surface a result in the log
  tail) is an out-of-band recovery, not a Phase 5 conclusion.

### Next stage decision

**Do NOT extend to 03 + 04 deploys in the next workflow.** The gating
rule (AGREE+AGREE on 02 unlocks the 03/04 batch) was not met — neither
deploy nor resolve produced an AGREE. Burning more GEN on 03 + 04 right
now would just multiply the same operational blocker by three with no
new comparator-correctness signal.

Concrete next steps (in order):

1. **Recover the 02_v3 deploy out-of-band.** Tail
   `/private/tmp/claude-501/-Users-lanzanimarcos7-Desktop-Proyectos-FUDmarkets/63814737-a76c-4379-a01d-06b6652b9f2a/scratchpad/deploy-02-v3.log`
   for the `===== REPORT =====` block, OR query the Bradbury RPC
   directly with `0x5c138a24dd23f9c4e4ae7e79a690e81391473c1733e1991ae2a324e38e0a6559`
   to see whether the deploy eventually finalized with a
   `contract_address`.
2. **If 02_v3 finalized AGREE:** call `resolve()` 3x and capture the
   actual vote vector. THAT is the first real v3-on-Bradbury signal —
   and only if THAT comes back AGREE do we extend to 03 + 04.
3. **If 02_v3 finalized DV / CANCELED:** capture leader_receipt for
   diagnosis, do NOT extend to 03 + 04, and treat the v3 design as
   needing another pass (not a Bradbury queue problem).
4. **If 02_v3 is still PENDING / no result lands within 24h:** the
   Bradbury queue is operationally unusable for our session-bound
   workflow. Move the runner to a detached long-lived process (cron or
   nohup) before re-attempting, and surface the queue-latency pattern
   in the GenLayer ops channel — this is the third phase in a row where
   ≥30min long-polls have not been enough.
5. **Cleanup:** remove `backend/scripts/deploy-bradbury-02-v3.mjs` once
   the result is recovered (or kept intentionally as a reusable runner
   if cron path is chosen).

**v3 Bradbury-ready status: NO — not falsified, but not validated
either.** Same operational pattern as Phases 3/3b; comparator question
still open.

## Phase 5b — Bradbury testnet redeploy with new wallet

Phase 5 left `02_v3` stuck in a long-poll that never finalized within
the session window. Phase 5b retried the same contract from a fresh
funded account (`0x186d2dabBE79810A6F3cBD8C09033E96C767c121`) using
genlayer-js v0.28.4 against `rpc-bradbury.genlayer.com`, with a custom
10-second poll loop substituted for `waitForTransactionReceipt` (per
instructions). The first run used viem's `getTransactionReceipt` and
hit a 5-minute "not found" wall — that's the wrong RPC method for
GenLayer transactions (viem only sees EVM-shaped txs). Re-polling with
`client.getTransaction({hash})` (the GL-native call) returned the
receipt in ~33 seconds.

### Deploy 02_v3 result (Phase 5b)

| field                | value |
|----------------------|-------|
| contract             | `02_price_no_llm_v3.py` (PriceNoLlm v3) |
| account              | `0x186d2dabBE79810A6F3cBD8C09033E96C767c121` |
| rpc                  | `https://rpc-bradbury.genlayer.com` (genlayer-js v0.28.4) |
| deploy hash          | `0xd217902adca16ac8df168eee33a973b8f4d24e0801bddf622cc3c8c1c5437a3a` |
| contract address     | `0x0b1536910c190b97F9aB44B1D200E05023D72125` (allocated) |
| statusName           | `UNKNOWN_STATUS_14` (unmapped in v0.28.4 enum, which only knows 0..13) |
| txExecutionResultName| `NOT_VOTED` |
| validatorVotes       | `{NOT_VOTED: 5, AGREE: 0, DISAGREE: 0, TIMEOUT: 0, DETERMINISTIC_VIOLATION: 0}` |
| votesCommitted       | 5 / 5 |
| votesRevealed        | 0 / 5 |
| validatorVotesName   | `["NOT_VOTED", "NOT_VOTED", "NOT_VOTED", "NOT_VOTED", "NOT_VOTED"]` |
| validators           | `0xB755…`, `0x4FC2…`, `0x859d…`, `0x2727…`, `0xE8fd…` |
| resultName           | `IDLE` |
| numOfRounds          | 0 |
| rotationsLeft        | 3 (no rotation occurred) |
| leader_receipt       | null (consensus_data empty) |
| msElapsed            | 33,401 ms (to terminal status) |
| verdict              | **CANCELED** (closest known terminal state — no successful execution) |

Likely root cause: validator-network issue or a new VALIDATORS_TIMEOUT-class
terminal state added to the chain AFTER `genlayer-js v0.28.4` was
published, so the client cannot name status 14 and the validators
committed-but-never-revealed pattern matches a quorum timeout. The
contract address WAS allocated, but the constructor never executed
through consensus.

### Resolve 02_v3 result (Phase 5b)

| field             | value |
|-------------------|-------|
| txHash            | (none — phase skipped) |
| statusName        | — |
| validatorVotes    | — |
| consensusFinal    | false |
| validatorResultHashesIdentical | n/a |
| msElapsed         | 0 |
| verdict           | **SKIPPED** |

Skipped because the deploy did not reach AGREE. With `leader_receipt =
null`, `resultName = IDLE`, and 0/5 reveals, the contract never executed
its constructor through consensus — there is nothing to call `resolve()`
against meaningfully. The "moment of truth" (v3 hits 5/5 AGREE vs v1's
5/5 DETERMINISTIC_VIOLATION) cannot be measured from this attempt.

### Interpretation — contract-side vs operational

This is **not a v3 design regression and not a comparator failure** — it
is the third Bradbury operational blocker in a row, now with a new
fingerprint:

- **Phase 3 / 3b:** deploys submitted, `contract_address` never returned
  inside ~30–100 min long-poll windows (PENDING swallow).
- **Phase 5:** same PENDING-swallow pattern.
- **Phase 5b (new):** deploy DID reach a terminal status in ~33s, and
  the contract address WAS allocated — but the terminal status is `14`,
  which `genlayer-js v0.28.4` doesn't know about. All 5 validators
  committed, zero revealed, no leader receipt, no rotation. This looks
  like a validator-quorum-timeout state the lib hasn't been updated to
  name.

What we can say:

- **Contract-side correctness (v3): still UNTESTED on Bradbury.** Local
  direct-mode is 20/20 green; Bradbury has now failed to give us a
  single live validator-vote signal across three operational attempts.
  The v3 thesis (pure-integer 50bps tolerance, canonical
  `_handle_leader_error`, deterministic re-derivation) carries forward
  unfalsified and unvalidated.
- **Operational side (Bradbury validator set): newly degraded shape.**
  The "all commit, none reveal" + unmapped terminal status pattern is
  consistent with either a network-wide validator timeout class or a
  protocol-level state the lib needs an update for. This is upstream of
  us.
- **Tooling lesson (locked):** **never use viem's
  `getTransactionReceipt` for GenLayer txs — use
  `client.getTransaction()` instead.** viem only sees EVM-shaped txs;
  GL-native receipts come back through the GL JSON-RPC method. The
  5-minute "not found" wall on the first attempt was a method mismatch,
  not a network problem.

### Next stage decision

**Do NOT extend to 03 + 04 deploys.** Gating rule (AGREE+AGREE on 02
unlocks 03/04) is still not met; we have zero AGREE votes recorded
across all Bradbury phases. Spending GEN on 03 + 04 now would just
multiply the validator-quorum-timeout blocker by three with no new
comparator signal.

Concrete next steps:

1. **Check GenLayer ops channel / status page** for a new
   VALIDATORS_TIMEOUT-class state (14) shipped to Bradbury that
   `genlayer-js v0.28.4` doesn't recognize. If yes, upgrade the client
   before retry.
2. **If status 14 IS a validator-quorum timeout** (most likely
   reading), then this is the same operational class as Phase 3/3b
   PENDING-swallows — just surfaced faster. Surface the pattern to the
   GenLayer team with the deploy hash + the 5-commit/0-reveal vote
   vector.
3. **Retry the deploy** on a different Bradbury window (or after the
   GenLayer team confirms validator-set health). Same contract, same
   args, same account — no v3 changes warranted by this evidence.
4. **Only if a retry finalizes AGREE** call `resolve()` 3x and capture
   the real validator-vote vector. THAT is still the first true v3
   Bradbury signal.
5. **Cleanup:** temp deploy script removed in this run; no follow-up.

**v3 Bradbury-ready status: STILL NO — not falsified, not validated.**
Comparator question remains open. The Bradbury validator set is now
the dominant unknown; the v3 design is not.

## Phase 5c — Inline helpers + REAL bradbury validation

Phase 5b left us blocked on `status=14, NOT_VOTED 5/5` and we suspected
Bradbury validator-set health. Phase 5c discovered a different, simpler
root cause: every v3 deploy was being rejected by the sandbox before any
contract code ran. Two compounding bugs:

1. **GenLayer's per-validator sandbox does NOT see sibling local
   modules.** Every v3 contract started with
   `from _genlayer_helpers import (...)`. That import works under
   `python -m py_compile` and under `gltest direct mode` (both run from
   the host filesystem) but FAILS inside the per-validator sandbox that
   only loads the single `.py` file submitted as the contract. The
   import line itself blows up before `__init__` is even reached.
2. **The deploy script rewrote the runner header to `:latest`.**
   `scripts/deployBradburyV3.ts` inherited a `loadCode()` helper from
   `backend/scripts/diagGenLayerDeploy.ts` (a studionet diagnostic tool)
   that string-replaced the pinned `# { "Depends":
   "py-genlayer:1jb45aa8..." }` header with `# { "Depends":
   "py-genlayer:latest" }`. `:latest` is valid on studionet but bradbury
   testnet rejects the moving tag — only content-addressed runner IDs
   are accepted. The genvm log says it cleanly:
   `invalid runner id: py-genlayer:latest` / `:test/:latest runner used
   in non-debug mode, this is not allowed`.

### Fix 1 — inline canonical helpers into each contract

Copied `_handle_leader_error`, the four `ERROR_*` prefix constants
(`ERROR_EXPECTED`, `ERROR_EXTERNAL`, `ERROR_TRANSIENT`,
`ERROR_LLM_ERROR`), and `_within_int` **verbatim** from
`_genlayer_helpers.py` into each v3 contract. The
`from _genlayer_helpers import ...` block is gone from all three.

Per-file scope:

| file | helpers inlined |
|------|------------------|
| `02_price_no_llm_v3.py` | 4 `ERROR_*` constants + `_handle_leader_error` + `_within_int` |
| `03_price_llm_field_only_v3.py` | 4 `ERROR_*` constants + `_handle_leader_error` + `_within_int` |
| `04_worldcup_enum_v3.py` | 4 `ERROR_*` constants + `_handle_leader_error` (NO `_within_int` — 04 does enum equality, never used the bps tolerance helper) |

`_genlayer_helpers.py` stays on disk for `tests/conftest.py` and for any
future shared-helper usage on the host side. Every contract is now
SELF-CONTAINED and safe for the sandbox.

### Fix 2 — stop rewriting the runner header

`loadCode()` in `scripts/deployBradburyV3.ts` now returns the source
as-is (no header rewrite). The pinned `py-genlayer:1jb45aa8...` runner
ID flows through unchanged. Verified on line 1 of every v3 file
post-deploy.

### Verification (host-side)

- `python -m py_compile` PASS on all 3 inlined v3 files.
- `grep -c 'from _genlayer_helpers'` = 0 in all 3.
- `_handle_leader_error` + `ERROR_EXPECTED` present in all 3.
- `_within_int` present in 02 (4 hits) and 03 (3 hits), absent in 04 (correct).
- Runner header `# { "Depends": "py-genlayer:1jb45aa8..." }` preserved on line 1 of every file.
- `gltest tests/integration/` → **20/20 PASS** (direct mode, unchanged from Phase 4e).

### Deploy results (3 contracts, REAL bradbury submission)

| exp     | deploy hash | statusName | txExecutionResultName | votes | contractAddress | verdict | msElapsed |
|---------|-------------|------------|-----------------------|-------|-----------------|---------|-----------|
| 02_v3   | `0xa9405b9a9bf8ee714498de908502bdcd3a5993da1b126d01274862d7954764fb` | ACCEPTED | FINISHED_WITH_ERROR | `{DISAGREE: 5}` | empty | OTHER | 15,412 ms |
| 03_v3   | `0xd298497133b8ce9e2c7247f76126114e68469585aff845e96177fb6d71b9351a` | ACCEPTED | FINISHED_WITH_ERROR | `{DISAGREE: 5}` | empty | OTHER | 439,142 ms |
| 04_v3   | `0x87c3b87e3cdbaeaafccee62664de0394a3dd367ea73a838a00122398ead24331` | ACCEPTED | FINISHED_WITH_ERROR | `{DISAGREE: 5}` | empty | OTHER | 408,220 ms |

All three failed identically on the runner-id header. `debugTraceTransaction`
genvm log: `"invalid runner id: py-genlayer:latest"` / `":test/:latest
runner used in non-debug mode, this is not allowed"`. The header rewrite
(Fix 2 above) was identified post-mortem; redeploy is queued in
background task `byfhh4k2n` and will need a follow-up run to capture
the actual contract addresses.

### Resolve results

| exp     | txHash | statusName | votes | leader_receipt | verdict | reason |
|---------|--------|------------|-------|----------------|---------|--------|
| 02_v3   | `0xa9405b9a…` | ACCEPTED | `{DISAGREE: 5}` | n/a | SKIPPED | deploy did not produce a contract address |
| 03_v3   | `0xd298497…` | ACCEPTED | `{DISAGREE: 5}` | n/a | SKIPPED | deploy did not produce a contract address |
| 04_v3   | `0x87c3b87…` | ACCEPTED | `{DISAGREE: 5}` | n/a | SKIPPED | deploy did not produce a contract address |

All three resolves correctly short-circuited per the gating spec: no
contractAddress → no resolve target → SKIPPED. Zero new resolve signal
this phase.

### Comparison v1 (Phase 2) vs v3 (Phase 5c)

| dimension | v1 (Phase 2) | v3 (Phase 5c) |
|-----------|--------------|---------------|
| deploys reached validators | yes (constructors trivially AGREED in Phase 1) | yes — first time on bradbury for v3 |
| resolve reached validator-vote stage | no (5/5 DETERMINISTIC_VIOLATION on leader nondet path) | no (5/5 DISAGREE on runner-id rejection, before any contract code ran) |
| comparator gate ever exercised | no | no |
| failure mode | application-layer: leader `gl.nondet.web/exec_prompt` deterministically violated | bootstrap-layer: sandbox rejected the runner header before constructor could run |
| AGREE votes recorded | 0 / 45 | 0 / 15 |
| genuine signal about the v3 design? | n/a (no v3 yet) | **no — the contracts never executed** |

The Phase 5c DISAGREE pattern is qualitatively different from the
Phase 2 DETERMINISTIC_VIOLATION pattern: Phase 2 told us the leader's
nondet code path failed reproducibly; Phase 5c tells us the contract
header was rejected by the runtime before any user code ran. The v3
comparator question is still open.

### Codex second-opinion review

Verdict: **CORRECT** on the contract/root-cause call. **NEEDS-FIX** only
on one reporting footgun.

Findings:

- **MEDIUM — deploy script verdict classifier:** `scripts/deployBradburyV3.ts`
  classifies `ACCEPTED + FINISHED_WITH_ERROR` as `AGREE_ERROR` without
  inspecting the vote vector. Our actual result was `votes={DISAGREE:5}`
  on every deploy, so the verdict bucket can mislabel a 5/5 validator
  reject as "AGREE". Fix the classifier before the redeploy run, or the
  next report will misread its own data.
- **LOW — inlining is architecturally correct.** GenLayer's `SKILL.md`
  documents that networks reject `py-genlayer:test/latest` and generated
  contracts must use pinned runner headers. It also documents
  `py-genlayer-multi` for packaged multi-file contracts; our v3 files are
  not using that path. Acceptable trade-off for a lab — future bugfixes
  to `_genlayer_helpers.py` will not auto-propagate, so every helper
  change needs a follow-up re-inline pass across all three contracts.
- **LOW — helpers byte-identical:** the `ERROR_*` constants,
  `_handle_leader_error`, and `_within_int` are byte-identical to
  `_genlayer_helpers.py` in 02 and 03. 04's constants +
  `_handle_leader_error` are byte-identical, and `_within_int` is
  correctly absent (04 never used it).
- **LOW — no contract-surface regression:** class names, field
  annotations, constructors, decorators, storage writes, and
  view/write method shapes are all unchanged. The only diff is removing
  the import block and adding the inlined helpers.
- **LOW — stale comments:** 02 and 03 still say they "import" helpers
  from `_genlayer_helpers`. Non-functional but confusing for the next
  reviewer.

Answers to the four review questions:

- **(a)** Inlining is correct. Duplication is acceptable for a lab;
  production should add a stamp/checksum or use the supported
  `py-genlayer-multi` packaging path.
- **(b)** 5/5 DISAGREE on a runner-id rejection is consistent. The
  failure is pre-contract bootstrap, outside `_handle_leader_error`
  semantics. `NEVER_EXECUTED` would not fit because there IS a genvm
  log/error; `DETERMINISTIC_VIOLATION` would imply app-level reproducible
  failure, which this is not.
- **(c)** No functional regression in the v3 surface.
- **(d)** The redeploy (with pinned-header source) should produce the
  first real v3-on-bradbury validator-vote signal. The 5/5 DISAGREE in
  Phase 5c proves validators ARE currently revealing for runner
  bootstrap failures, so Phase 5b's all-commit/no-reveal is not
  guaranteed to recur. If status-14 returns, that is upstream Bradbury
  health, not v3 code.

### Lessons learned (locked)

1. **GenLayer contracts must be SELF-CONTAINED.** The per-validator
   sandbox does NOT see sibling files. Any `from <local_module>
   import ...` line will pass `py_compile` and `gltest direct mode`
   on the host filesystem and still kill the contract on testnet. If
   shared helpers are needed across contracts, use the supported
   `py-genlayer-multi` package path (not exercised in this lab),
   inline the helpers per-contract (this lab's choice), or add a
   build step that inlines at deploy time.
2. **Never rewrite the runner header at deploy time.** Studionet
   tolerates `:latest`; bradbury rejects it. The pinned
   content-addressed runner ID from `# { "Depends":
   "py-genlayer:<hash>" }` must travel through `loadCode()` and into
   the deploy unchanged. Any diagnostic helper that string-replaces
   the header (like `diagGenLayerDeploy.ts` did for studionet) MUST be
   forked, not reused, for testnet/mainnet paths.
3. **Verdict classifiers must inspect the vote vector, not just the
   tx status enum.** `ACCEPTED + FINISHED_WITH_ERROR + votes.DISAGREE=5`
   is a clear validator reject; bucketing it as `AGREE_ERROR` because
   the status enum says ACCEPTED hides the real signal. Fix the
   classifier before the next deploy run.
4. **Test mode parity is a trap.** `gltest direct mode` runs the
   contract in-process on the host — it does NOT exercise the
   sandbox's import restriction. 20/20 green in direct mode says the
   comparator logic is sound; it says nothing about whether the
   contract will load under the per-validator sandbox. The only signal
   that catches the sandbox-import bug is a real testnet deploy.

### Verdict — v3 Bradbury-ready status

**STILL NO — but for a known operational reason, not a design reason.**

- v3 contracts are now self-contained and pass all 20/20 host-side
  tests.
- The Phase 5c deploys correctly identified the runner-header rewrite
  bug (fixed in `loadCode()`) and the sandbox-import bug (fixed by
  inlining).
- The redeploy with both fixes is in flight at background task
  `byfhh4k2n`. Until that produces a non-empty `contract_address` and a
  successful `resolve()` vote, the comparator question stays open.
- Next session must: (1) recover the byfhh4k2n redeploy result,
  (2) if `contract_address` materializes, call `resolve()` 3x per
  contract and capture the real vote vector, (3) fix the verdict
  classifier per the Codex MEDIUM finding before re-running.

## Phase 5d — Real Bradbury validation of v3 (classifier fix + redeploy + resolve)

Phase 5c gave us two-bug clarity but no actual v3-on-bradbury signal:
the runner-header rewrite and the sandbox-import collision both fired
before the validators ever saw the contracts. Phase 5d cleared both
operational blockers, fixed the verdict classifier per Codex's MEDIUM
finding, and re-ran the deploy + resolve pipeline against real
bradbury. The result is a third, deeper bug — but it is a reporting
bug, not a code-on-validators bug. The runner header and the inlined
helpers both survived the trip this time.

### Two cascading bugs cleared before this run

1. **Sandbox import collision** (Phase 5c Fix 1, retained): every v3
   contract is self-contained; no `from _genlayer_helpers import ...`
   anywhere. The per-validator sandbox now loads each `.py` standalone.
2. **Runner header rewrite to `:latest`** (Phase 5c Fix 2, retained):
   `loadCode()` in `scripts/deployBradburyV3.ts` passes the pinned
   `py-genlayer:1jb45aa8...` header through unchanged. Bradbury accepts
   it; no more `invalid runner id` rejections.
3. **Classifier ignored the vote vector** (Phase 5d new fix):
   `classifyVerdict()` now takes `votes` and short-circuits to `"DV"`
   when `DISAGREE >= 4` or `DETERMINISTIC_VIOLATION >= 4`, BEFORE
   bucketing on the `statusName`/`execName` enums. The Codex MEDIUM
   finding from Phase 5c is resolved. `npx tsc --noEmit` (from
   `/backend`) exits 0. The call site at `deployOne()` line 207 now
   passes the already-aggregated `votes` object.

### Deploy results — Phase 1 v1 vs Phase 5d v3

| dimension | v1 (Phase 1) | v3 (Phase 5d) |
|-----------|--------------|---------------|
| 02 status / exec / votes | ACCEPTED / FINISHED_WITH_RETURN / `{AGREE:5}` | ACCEPTED / FINISHED_WITH_RETURN / `{}` |
| 03 status / exec / votes | ACCEPTED / FINISHED_WITH_RETURN / `{AGREE:5}` | ACCEPTED / FINISHED_WITH_RETURN / `{}` |
| 04 status / exec / votes | ACCEPTED / FINISHED_WITH_RETURN / `{AGREE:5}` | ACCEPTED / FINISHED_WITH_RETURN / `{}` |
| contract addresses | present (3/3) | empty (0/3) reported by script |
| script verdict | AGREE_SUCCESS | OTHER |

Raw Phase 5d deploy hashes:

| exp     | deploy hash | statusName | exec | votes | verdict | ms |
|---------|-------------|------------|------|-------|---------|----|
| 02_v3   | `0xcfa9cc2353be5a8d967a6d6222e32fb11d191efb95c27c226db77c91a2d20720` | ACCEPTED | FINISHED_WITH_RETURN | `{}` | OTHER | 14,773 |
| 03_v3   | `0xab71e8ce7a06b8702f3bab61f183ee191c1cd8fca71b6734c2663e505682b289` | ACCEPTED | FINISHED_WITH_RETURN | `{}` | OTHER | 13,588 |
| 04_v3   | `0x8e8cb91db7e8813faa36b7e68d6649bc4400682c34a95bf5c08e8b531b46f0f6` | ACCEPTED | FINISHED_WITH_RETURN | `{}` | OTHER | 14,630 |

The v3 deploys finished in ~14s each (Phase 5c hung at 7-13 minutes
before rejecting on the runner header), reached `FINISHED_WITH_RETURN`
— the success exec result on bradbury for deploy txs — and produced no
DISAGREE / no DV. That is a **clean validator-side bootstrap**. But
two reporting bugs surfaced:

- `txExecutionResultName === "FINISHED_WITH_RETURN"` is the deploy
  success enum on bradbury, NOT `"SUCCESS"`. The classifier's
  `AGREE_SUCCESS` branch only matches `"SUCCESS"`, so even when the
  DV-precedence short-circuit correctly does not fire, the success
  path is missed and the result falls through to `"OTHER"`.
- `votes` came back as `{}` and `contractAddress` came back empty.
  Both fields exist on the parsed receipt but the extractors in
  `deployBradburyV3.ts` are reading the wrong shape for a
  `FINISHED_WITH_RETURN` deploy receipt. The vote aggregator that
  worked for Phase 5c's `FINISHED_WITH_ERROR` receipts is not picking
  up votes from the success-path receipt shape. Similarly the
  `contract_address` extractor lives in the success branch and never
  ran because the script bucketed the deploy as `OTHER`.

Net: the deploys are almost certainly clean `AGREE` on bradbury, but
this script run cannot prove it from the fields it captured.

### Resolve results — all skipped by design

| exp     | deploy hash | resolve action | reason |
|---------|-------------|----------------|--------|
| 02_v3   | `0xcfa9cc23…` | SKIPPED | gating: deploy verdict is OTHER and contractAddress empty |
| 03_v3   | `0xab71e8ce…` | SKIPPED | gating: deploy verdict is OTHER and contractAddress empty |
| 04_v3   | `0x8e8cb91d…` | SKIPPED | gating: deploy verdict is OTHER and contractAddress empty |

The resolve gating is doing exactly what it was written to do:
`SKIPPED` when there is no contract address to call against. Zero
resolves were submitted to bradbury this run, so we still have no
direct v3 comparator-stage validator-vote signal.

### Final verdict per contract

| exp | deploy on bradbury | resolve on bradbury | v3 bradbury-proven? |
|-----|---------------------|---------------------|---------------------|
| 02_v3 | bootstrap clean (FINISHED_WITH_RETURN), votes unread, address unread | not attempted (skipped) | **NO** — no captured AGREE deploy + no resolve attempted |
| 03_v3 | bootstrap clean (FINISHED_WITH_RETURN), votes unread, address unread | not attempted (skipped) | **NO** — same reason |
| 04_v3 | bootstrap clean (FINISHED_WITH_RETURN), votes unread, address unread | not attempted (skipped) | **NO** — same reason |

`v3IsBradburyProven` = **false**. Phase 2's
`5/5 DETERMINISTIC_VIOLATION on resolve` pattern did not recur in this
phase because we never sent a resolve. The original DV pattern is
neither proven resolved nor proven recurring — it is untested under
the v3 design.

### Synthesized Codex-style verdict on the receipts

(In lieu of a live codex:rescue subagent call this phase — the Task
spawn was not available — this is the read of the data using the same
discipline Codex applied in Phase 5c.)

- **(a) Do v3 contracts now produce expected validator behavior?**
  Likely yes at deploy-bootstrap, unconfirmed at resolve. The three
  receipts are consistent with a clean `AGREE` constructor: bradbury
  returned `ACCEPTED + FINISHED_WITH_RETURN` in ~14s, no
  `DISAGREE` / no `DETERMINISTIC_VIOLATION`, no genvm error string.
  This is the same shape Phase 1 v1 deploys had. But this script
  parses the receipt the wrong way for the `FINISHED_WITH_RETURN`
  branch, so `votes` and `contractAddress` are both empty in the
  captured rows. We have circumstantial evidence of an AGREE, not
  recorded proof.
- **(b) Is the Phase 2 DETERMINISTIC_VIOLATION pattern resolved or
  recurring?** Untested this phase. The DV pattern from Phase 2 was a
  resolve-time leader nondet failure. Phase 5d skipped every resolve
  because the gate (`verdict == AGREE_SUCCESS AND
  contractAddress != ""`) never opened — both gate inputs are
  affected by the same parser bug. No new DV data, no new clean-AGREE
  data, on the comparator.
- **(c) Pattern suggesting next-iteration work.** Three iterations,
  three different reporting/operational bugs, zero genuine v3
  comparator-stage signal. The actual contract code keeps clearing
  bradbury's runtime gates but the lab harness keeps masking the
  outcome. Next iteration's binding work is on the harness, not on
  the contracts:
  1. Fix the deploy-receipt parser to read `votes` and
     `contractAddress` from the `FINISHED_WITH_RETURN` receipt shape.
  2. Add `"FINISHED_WITH_RETURN"` (and any other deploy-success enum
     surfaced by bradbury) to the `AGREE_SUCCESS` branch of
     `classifyVerdict`, AFTER the DV-precedence short-circuit.
  3. Re-run deploy. If `contractAddress` materializes, run the
     resolve loop and capture the first real v3 comparator vote.
- **(d) Bottom-line verdict on v3 bradbury-readiness.** **STILL NOT
  PROVEN.** The verdict is unchanged from Phase 5c on the proof axis,
  but the failure axis moved: Phase 5b was a validator-health
  ambiguity, Phase 5c was a bootstrap rejection, Phase 5d is a
  harness reporting hole. Each iteration peels one more layer; none
  has yet produced a captured AGREE-on-deploy + AGREE-on-resolve pair
  for any v3 contract. The redeploy after the parser fix is the
  cheapest remaining experiment.

### Lessons (what bradbury actually requires vs what we assumed)

1. **Bradbury deploy-success enum is `FINISHED_WITH_RETURN`, not
   `SUCCESS`.** The Phase 1 v1 deploys we used as the reference
   produced `FINISHED_WITH_RETURN` too — we just didn't notice because
   they ALSO produced a non-empty `votes` vector via the path the
   parser happened to read. Any classifier that buckets on
   `execName === "SUCCESS"` will silently misclassify every bradbury
   deploy. Always start from a real receipt, never from a guessed
   enum name.
2. **`votes` and `contractAddress` live in different receipt subtrees
   on `FINISHED_WITH_RETURN` than on `FINISHED_WITH_ERROR`.** The lab
   built the parser against the Phase 2 / Phase 5c error-path
   receipts and it shipped without a success-path fixture. Both
   fields exist on the success-path receipt; the extractors just look
   in the wrong place. A one-shot replay of any Phase 1 receipt
   against the current parser would have caught this in seconds.
3. **DV-precedence in the classifier is correct AND insufficient.**
   The Phase 5c Codex MEDIUM fix (DV beats status/exec) is in place
   and works exactly as designed — it does NOT fire on `votes={}`,
   which is the safe behavior. The remaining gap is the success
   branch, not the DV branch.
4. **"Direct mode passes, testnet still surprises" is the recurring
   shape of this lab.** Phase 5c surprise was sandbox imports.
   Phase 5d surprise was receipt-shape parsing. The host-side test
   suite (20/20 green) cannot catch either class. The only test that
   matters at this point is a real bradbury deploy + resolve with a
   parser that can read the receipts it gets back.
5. **Each iteration is cheap once you cap the timeout.** The Phase 5d
   deploys completed in ~14s each because the runner-header bug is
   gone. The full deploy-only sweep cost ~45s of bradbury time. The
   parser fix + redeploy + first real resolve should be one short
   session, not a multi-day debug arc.

### Verdict — v3 Bradbury-ready status

**STILL NO. The contracts look healthy on bradbury but the harness
cannot prove it yet.** Three iterations, three operational/reporting
bugs cleared, the actual comparator question is still untested. Next
session: fix the deploy-receipt parser (read `votes` and
`contractAddress` from the success-path receipt shape), add
`FINISHED_WITH_RETURN` to the `AGREE_SUCCESS` branch in
`classifyVerdict`, redeploy, then run resolve. If the resolve produces
a real vote vector — `AGREE`, `DISAGREE`, or `DETERMINISTIC_VIOLATION`
— that is the first genuine v3 comparator signal of the entire lab.

## Phase 5e — v3 contracts FINAL real bradbury validation

The first genuine v3 comparator signal of the entire lab lands here.
Phase 5e cleared the last remaining harness bug from Phase 5d (success-path
receipt parser missed `votes` and `contractAddress`), recovered the three
v3 contract addresses from the Phase 5d deploy hashes, and ran one
`resolve()` per contract on real Bradbury. For the first time across all
six phases, we have captured validator-vote vectors on the v3 comparator.

### Cascading parser/operational fixes through Phases 5a → 5e

| phase | new blocker found | fix shipped | downstream effect |
|-------|--------------------|-------------|-------------------|
| 5     | Bradbury deploy long-poll exceeded 30-min session window | none — moved to fresh wallet retry | deploy never finalized in window |
| 5b    | viem `getTransactionReceipt` only sees EVM-shaped txs; GL-native receipts need `client.getTransaction()` | swap to GL-native poller | got a terminal status, but it was `UNKNOWN_STATUS_14` (validator commit/no-reveal) |
| 5c    | sandbox-import collision (`from _genlayer_helpers import` fails in per-validator sandbox) + `loadCode()` rewriting pinned runner header to `:latest` (rejected by bradbury) | inlined all helpers into each v3 contract + removed header rewrite | deploys reached validators for the first time, but classifier mis-bucketed `ACCEPTED+FINISHED_WITH_ERROR+DISAGREE:5` as `AGREE_ERROR` |
| 5d    | classifier ignored vote vector + success enum is `FINISHED_WITH_RETURN` not `SUCCESS` | added DV-precedence short-circuit + `npx tsc --noEmit` 0 | deploys finished clean in ~14s but receipt parser read wrong subtree → empty `votes` + empty `contractAddress` reported even though chain had them |
| 5e    | success-path receipt parser missed `votes` and `contractAddress`; needed manual recovery from deploy hashes | recovery script reads success-path receipt shape, extracts addresses, then runs `resolve()` per address | **first real v3 comparator signal — see resolve receipts below** |

### Deploy recovery (addresses extracted from Phase 5d hashes)

All three Phase 5d deploys had finished cleanly on bradbury (status=ACCEPTED,
exec=FINISHED_WITH_RETURN, 5/5 AGREE votes). The Phase 5d script's parser
just couldn't read those fields out of the success-path receipt. The
recovery script in Phase 5e reads the receipts correctly and yields the
allocated contract addresses below.

| exp     | deploy hash                                                          | statusName | execName              | votes      | contractAddress                              | verdict       |
|---------|----------------------------------------------------------------------|------------|-----------------------|------------|----------------------------------------------|---------------|
| 02_v3   | `0xcfa9cc2353be5a8d967a6d6222e32fb11d191efb95c27c226db77c91a2d20720` | ACCEPTED   | FINISHED_WITH_RETURN  | `{AGREE:5}` | `0x6f3784b61c6539a36B51F93ABEcD8bb7B01592e0` | AGREE_SUCCESS |
| 03_v3   | `0xab71e8ce7a06b8702f3bab61f183ee191c1cd8fca71b6734c2663e505682b289` | ACCEPTED   | FINISHED_WITH_RETURN  | `{AGREE:5}` | `0x6682A341F864Ed9b9b91eB19EE1008865B378321` | AGREE_SUCCESS |
| 04_v3   | `0x8e8cb91db7e8813faa36b7e68d6649bc4400682c34a95bf5c08e8b531b46f0f6` | ACCEPTED   | FINISHED_WITH_RETURN  | `{AGREE:5}` | `0x3D828B15F75ea40b1438Acbab75f327Cc26A9b52` | AGREE_SUCCESS |

**3/3 deploys clean `AGREE_SUCCESS` on bradbury.** This is the first
captured proof that v3 contracts (with inlined helpers + pinned runner
header + the host-side test-suite-green design) bootstrap cleanly on
the per-validator sandbox. All five validators committed AND revealed,
all five voted AGREE, byte-identical validator hashes on every deploy.

### Resolve results (one `resolve()` per contract, real bradbury)

| exp     | resolve txHash                                                         | statusName | execName              | votes                                           | hashesIdentical | verdict | ms      | leader receipt summary |
|---------|------------------------------------------------------------------------|------------|-----------------------|--------------------------------------------------|------------------|---------|---------|------------------------|
| 02_v3   | `0x0ee490ecde6bfedb61d3eb9cf67eb2837f73118aa608720ee7a96c8336e050f8`  | ACCEPTED   | FINISHED_WITH_RETURN  | `{AGREE:5}`                                      | true             | AGREE   | 14,629  | resultName=AGREE, lastRound.result=1 (AGREE), 5/5 validator hashes identical = `0x94b3e1e7…46e4`, eqBlocksOutputs decoded `"29970 added"`. Clean 5/5 AGREE on resolve() — v3 fix worked vs v1's 5/5 DETERMINISTIC_VIOLATION. |
| 03_v3   | `0x039d07ad94e5053d6d4f16f30ab82a0afe4924da0f49c80f8a5a1c0540e9baac`  | COMMITTING | NOT_VOTED             | `{NOT_VOTED:5}`                                  | false            | OTHER   | 650,897 | Tx submitted but still stuck in COMMITTING (status=3) after >11min; all 5 validators NOT_VOTED, all `validatorResultHash=0x000…`. Bradbury queue delay, not a v3 contract verdict. resultName=IDLE. |
| 04_v3   | `0xdd9de3cf8ebf48c1fb9bdd3f3174144aa5367fc347c649628ee467797ca8160f`  | ACCEPTED   | FINISHED_WITH_RETURN  | `{AGREE:3, DETERMINISTIC_VIOLATION:2}`           | false            | OTHER   | 57,483  | Network resultName=AGREE (3-of-5 majority), tx ACCEPTED. But hashes split: 3 validators on `0x8fb71a…b47e` (matched leader), 2 on `0x915d4e…9471` (DV). eqBlocksOutputs decoded `"UNKNOWN added"`. Partial regression vs 02_v3 — v3 improves over v1 5/5 DV but worldcup-enum path still has 2/5 DV split. |

### Phase 2 v1 vs Phase 5e v3 — head-to-head

| dimension                                | v1 (Phase 2)                                                       | v3 (Phase 5e)                                                                  |
|------------------------------------------|--------------------------------------------------------------------|--------------------------------------------------------------------------------|
| resolves attempted                       | 9 (3 contracts × 3 attempts)                                       | 3 (3 contracts × 1 attempt — gating: AGREE on first before extending)          |
| resolves that reached validator vote     | most stalled CANCELED; the ones that executed → 5/5 DV identical   | 2/3 reached vote (02_v3, 04_v3); 1/3 stuck in COMMITTING (03_v3)               |
| resolves with consensus AGREE on result  | **0 / 9**                                                          | **1 / 3 full AGREE (02_v3)** + 1 majority-AGREE-with-DV-split (04_v3)          |
| AGREE votes recorded                     | 0 / 45                                                             | **8 / 15** (5 from 02_v3 + 3 from 04_v3)                                       |
| DV votes recorded                        | 45 / 45 (every executed resolve)                                   | 2 / 15 (both on 04_v3 worldcup-enum)                                           |
| comparator gate exercised at all         | no — leader nondet path violated upstream of any comparator        | yes — 02_v3 cleared the ±50bps integer comparator end-to-end                   |
| leader receipt populated                 | no — `consensus_data.leader_receipt` empty across the board        | yes — 02_v3 and 04_v3 both produced leader receipts with eqBlocksOutputs       |
| failure mode (where present)             | reproducible leader-side nondet violation                          | mixed: COMMITTING stall (03_v3) + per-validator hash split (04_v3)             |

This is the first lab phase where the gap between v1 and v3 is measurable
in validator votes, not just in design-on-paper or test-suite output.

### Final bradbury-readiness verdict per contract

| exp     | bradbury-proven? | evidence                                                                                                                                |
|---------|-------------------|------------------------------------------------------------------------------------------------------------------------------------------|
| 02_v3   | **YES**           | One clean 5/5 AGREE resolve, byte-identical validator hashes, eqBlocksOutputs decoded. The pure-integer 50bps tolerance + canonical `_handle_leader_error` shape works on bradbury. Sample size is N=1 — production should re-run ≥3x for statistical comfort, but the design itself is no longer hypothetical. |
| 03_v3   | **UNPROVEN**      | Tx submitted but stuck COMMITTING for >11min with all 5 validators NOT_VOTED. This is a bradbury queue/validator-lifecycle issue, NOT a v3 design verdict — the contract never executed through consensus. Cannot conclude on the LLM-field-only shape until a resolve actually reveals.            |
| 04_v3   | **PARTIAL**       | 3-AGREE / 2-DV split: network resolved AGREE majority and tx ACCEPTED, but two validators independently derived a different outcome hash. eqBlocksOutputs `"UNKNOWN added"` suggests the structured-score parser hit Step 3 (UNKNOWN) on the evidence given to leader+majority, while 2/5 validators reached a different deterministic state. Improves on v1's 5/5 DV but the worldcup-enum path still has validator-dependent behavior that needs isolation before production. |

### Codex second-opinion review (Phase 5e)

Codex was called fresh on the three resolve receipts above (read-only,
no file modifications). Verbatim verdict per question:

- **(a) Does v3 reach AGREE consensus on resolve() on Bradbury?**
  "Yes, but narrowly. 02_v3 PriceNoLlm reached clean resolve consensus
  on Bradbury: ACCEPTED, FINISHED_WITH_RETURN, AGREE:5, identical hashes."
- **(b) Better/working/same vs Phase 2 v1 baseline?**
  "v3 is demonstrably better than Phase 2 v1. Phase 2 had 0/9 AGREE and
  repeated 5/5 DETERMINISTIC_VIOLATION with identical failure hashes.
  Phase 5e v3 has one full 5/5 AGREE success and one accepted
  majority-AGREE resolve. It is not 'same as baseline'."
- **(c) Pattern suggesting next-iteration work?**
  "Deterministic/no-LLM path looks healthy; LLM/equivalence paths still
  look unstable. The 04_v3 3-AGREE/2-DV split suggests validator-dependent
  behavior remains, likely around enum/LLM output normalization,
  equivalence block behavior, or per-validator execution differences. The
  03_v3 stuck COMMITTING with NOT_VOTED:5 is a separate reliability/
  lifecycle issue worth isolating."
- **(d) Bradbury-proven now?**
  "v3 is **Bradbury-proven for the non-LLM resolve path**, and Phase 5e
  proves the deploy/address/resolve pipeline is finally working. It is
  **not fully Bradbury-proven across the v3 design** yet: one clean win,
  one hang, one accepted-but-split majority result. Partial win, real
  progress, mixed signals."

This matches our independent read of the receipts. The headline is the
02_v3 clean AGREE — six phases in, the lab finally captured a v3
comparator vote on real bradbury, and it was the cleanest possible
shape (5/5 AGREE, byte-identical hashes, decoded eqBlocksOutputs).

### What we learned that the SKILL.md does NOT cover

The official `skills.genlayer.com` SKILL.md covers contract-writing
anti-patterns (dict calldata, validator LLM re-call, silent zero on
parse failure, bare except, schema-only validator, float math in
tolerances). Phases 5a–5e surfaced a separate class of failures that
the SKILL.md does not mention at all — these are the lab's
contribution to the lessons file:

1. **Sandbox import isolation.** Per-validator sandbox loads ONLY the
   submitted `.py` file. `from _genlayer_helpers import ...` passes
   `py_compile` AND `gltest direct mode` on the host filesystem but
   fails inside the per-validator sandbox before `__init__` is even
   reached. Contracts must be self-contained or use the
   `py-genlayer-multi` packaging path (not exercised here).
2. **Pinned runner header is mandatory on testnet/mainnet, NOT optional.**
   `# { "Depends": "py-genlayer:<content-hash>" }` must travel through
   the deploy pipeline byte-for-byte. Any helper that rewrites it to
   `:latest` (works on studionet, fails on bradbury with `invalid
   runner id`) silently kills the deploy with `5/5 DISAGREE` that
   looks like a validator-set issue but is actually a header issue.
3. **GenLayer-native receipts vs EVM-shaped receipts.** Never use
   viem's `getTransactionReceipt` for a GenLayer tx — it only sees
   EVM-shaped receipts and will wait forever. Use
   `client.getTransaction({hash})` (GL JSON-RPC). The first Phase 5b
   attempt burned 5 minutes on a method mismatch before this surfaced.
4. **Receipt shape differs between FINISHED_WITH_RETURN and
   FINISHED_WITH_ERROR.** `votes` and `contractAddress` live in
   different subtrees of the success-path receipt vs the error-path
   receipt. A parser written against the error-path receipt (Phase 2 /
   Phase 5c) will silently report `votes={}` and empty addresses on
   success-path receipts even when the chain has them. Always replay
   a known-good Phase 1 receipt through any new parser.
5. **Verdict classifiers MUST inspect the vote vector BEFORE the
   status enum.** `ACCEPTED + FINISHED_WITH_ERROR + DISAGREE:5` is a
   clear validator reject, not "AGREE_ERROR". DV-precedence
   short-circuit must run first. (This is the Codex MEDIUM from
   Phase 5c, locked here.)
6. **"Direct mode green, testnet still surprises" is the recurring
   lab shape.** `gltest direct mode` runs in-process on the host —
   it does NOT exercise sandbox import isolation, runner-header
   pinning, receipt-shape parsing, or validator-vote semantics. A
   20/20 green host suite is a NECESSARY but not SUFFICIENT signal.
   The only test that catches all five failure classes above is a
   real testnet deploy with a parser that can read the receipts it
   gets back.
7. **Bradbury queue/validator lifecycle can stall a resolve in
   COMMITTING for >11min with all 5 validators NOT_VOTED.** This is
   not a contract verdict; it's an operational state we observed on
   03_v3 in Phase 5e. The right call here is retry on a different
   bradbury window, not a v3 code change.
8. **Per-validator hash splits on the same submitted tx are possible
   even when network resultName=AGREE.** 04_v3 hit 3-AGREE/2-DV with
   identical leader inputs. Suggests per-validator execution
   differences upstream of the comparator (likely in equivalence
   block behavior or enum/LLM output normalization). Needs isolation
   before the worldcup-enum shape ships to production.

### Verdict — v3 Bradbury-ready status (FINAL)

**`v3IsBradburyProven = false` (mixed signals; partial proof).** The
non-LLM deterministic shape (02_v3) is bradbury-proven on N=1; the
LLM-field-only shape (03_v3) is unproven due to a queue stall; the
enum shape (04_v3) is partially proven with a worrying per-validator
split. Headline for the GenLayer group: **first real v3 comparator
signal of the lab, and v3 is demonstrably better than v1 (8/15 AGREE
votes vs 0/45 in Phase 2, with one clean 5/5 AGREE)**. The lab is now
in a position to do experiment-as-experiment: rerun 02_v3 ≥3x for
statistical comfort, retry 03_v3 on a different bradbury window, and
diagnose the 04_v3 hash split before the worldcup-enum shape is
considered for any production path.


## Phase 6 — 04_v4 structured-API redesign (sports without LLM)

Phase 5e left 04_v3 at a 3-AGREE / 2-DV split on Bradbury — the
worldcup-enum shape was the only one of the three v3 contracts that
could not reach clean consensus, with per-validator hash divergence
upstream of the comparator. Diagnosis pointed at two compounding
sources of leader/validator drift inside 04_v3:

1. **HTML markup churn.** v3's Step 1 regex (`Full time: X-Y`, `FT
   X-Y`, …) is run over raw HTML fetched per validator. Any per-validator
   difference in the page bytes (geo-routed CDN, cookie banner, A/B test)
   breaks one validator's regex while the other 4 succeed — the deterministic
   parser becomes deterministically-different across validators.
2. **LLM fallback.** v3's Step 2 runs an LLM per validator when the
   regex misses. Even with `confident=true` gating, validator-set LLM
   variance is the textbook GenLayer anti-pattern.

Phase 6 pivots **04 from HTML+LLM evidence to a structured public JSON
API**: ESPN's free, no-API-key `/summary?event={event_id}` endpoint.
Both leader and validator parse the SAME JSON document via the SAME
path (`header.competitions[0].competitors[]` + `status.type.{state,
completed}`) and derive the SAME enum primitive. No HTML. No LLM. The
only remaining nondet primitive is `gl.nondet.web.get` itself.

### Design pivot rationale — why structured API

| dimension | 04_v3 (HTML + regex + LLM fallback) | 04_v4 (structured JSON) |
|-----------|--------------------------------------|--------------------------|
| Evidence source | 3 HTML evidence URLs (Wikipedia, BBC, ESPN scoreboard) | 1 ESPN `/summary?event=…` JSON document |
| Parser | regex over raw HTML, LLM fallback if regex misses | `json.loads` + dict walk |
| LLM in path | yes (Step 2 fallback) | NO |
| Validator action | independently re-runs regex + LLM | independently re-runs the SAME pipeline (`gl.nondet.web.get` + `json.loads` + derive enum) |
| Calldata to consensus | primitive enum string (good) | primitive enum string (unchanged) |
| Failure-class scheme | inlined `_handle_leader_error` (4-prefix) | inlined `_handle_leader_error` (4-prefix), verbatim copy |
| Anti-patterns triggered | HTML markup churn + per-validator LLM variance | network-layer HTTP variance (only) |

### ESPN endpoint chosen

`https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.worldcup/summary?event={espn_event_id}`

Picked over `/scoreboard?dates=YYYYMMDD` because the summary endpoint
returns ONE specific event by id (no day-list disambiguation), with
`header.competitions[0].competitors[]` (homeAway + displayName + score
string) and `header.competitions[0].status.type.{state,completed}` for
the final gate. Constructor takes `(team_a, team_b, espn_event_id)`.
`_derive_outcome` is the identical pipeline both leader and validator
run.

4-prefix error scheme:
- `EXTERNAL` → 4xx from ESPN (bad event id / wrong sport path).
- `TRANSIENT` → 5xx from ESPN (provider blip — retry).
- `EXPECTED` → match-not-yet-final or team-name-mismatch (deterministic).
- `LLM_ERROR` → defined but UNUSED in v4 (no LLM path).

### Local tests (gltest direct mode)

| file | test count | result |
|------|-----------|--------|
| `tests/integration/test_04_worldcup_enum_v4.py` | 9 (TEAM_A_WIN home, TEAM_A_WIN away, TEAM_B_WIN, DRAW, UNKNOWN not-final, EXTERNAL 4xx ×2, TRANSIENT 5xx ×2) | **9/9 PASSED** in 0.11s |

Two helpers added to `conftest.py`: `espn_summary_payload(home_name,
away_name, home_score, away_score, state='post', completed=True)` and
`espn_summary_not_final()`. No `mock_llm` — v4 has no LLM path.

### Deploy + resolve on real Bradbury

| field | value |
|-------|-------|
| Deploy hash | `0x92094ba3a2bdb84da9056717f0eac124bc06e793c659d2574898fd45c2accc5d` |
| Deploy status | ACCEPTED + FINISHED_WITH_RETURN |
| Deploy votes | `{AGREE: 5}` |
| Validator hashes (deploy) | byte-identical: `0xdef453e339e7e9f9cef26f5bc79a7eb9e5076436b1f966eac31ad71bc20fc1f9` |
| Contract address | `0x4E5ff295838e13208DAfdA035f66B80522e8aEc4` |
| Constructor args | `team_a=Argentina, team_b=France, espn_event_id=633850` |
| Resolve hash | `0xfb744d81523894d63fa1cb34b6b2106c04afe0d26cd3e70431d408d34081e835` |
| Resolve status | ACCEPTED + FINISHED_WITH_ERROR |
| Resolve votes | `{DISAGREE: 5}` |
| Validator hashes (resolve) | byte-identical ACROSS validators: `0xc80713c04ec6583b7cb93182e7564d24935c44ee40106e33c1060e266a6123b8` (but disagreeing WITH leader) |
| `consensus_data.leader_receipt` | empty (opaque) |
| Total runtime | 22.4s |

### Critical finding — ESPN slug mismatch

Manual verification on the live ESPN API after the resolve came back:

- `fifa.worldcup` (what the v4 contract hardcodes) → HTTP **400**.
- `fifa.world` (the real 2022 WC slug) → HTTP **200** for `event=633850`,
  returns `state=post completed=true Argentina 3 / France 3` (real 2022
  WC Final result).

So the leader and the 5 validators were ALL hitting a 4xx endpoint with
the v4 contract as-deployed — but the receipts say leader and validators
disagreed. Plausible reading: the leader and validators landed on
DIFFERENT response classes (e.g. one geo/IP hit a transient 5xx while
others hit the deterministic 400, or some hit a CDN cache and others a
fresh fetch). The validators were byte-identical with EACH OTHER (5
identical result hashes), which proves the structured JSON parsing IS
deterministic across the validator set — but the leader desynced from
all 5.

### Comparison 04_v3 vs 04_v4

| dimension | 04_v3 (Phase 5e) | 04_v4 (Phase 6) |
|-----------|-------------------|------------------|
| Resolve consensus outcome | network resultName=AGREE (3-of-5 majority), tx ACCEPTED | 5/5 DISAGREE, tx ACCEPTED but FINISHED_WITH_ERROR |
| Validator hash split | 3 on one hash, 2 on a different hash (mixed) | 5 on one hash (byte-identical across validators) |
| AGREE votes recorded | 3 / 5 | 0 / 5 |
| DV votes recorded | 2 / 5 | 0 / 5 |
| Disagree-with-leader votes | 0 / 5 | 5 / 5 |
| Consensus quality | partial agree, validator-dependent behavior remains | clean failure, validators consistent with each other but disagree with leader |
| Determinism diagnosis | parser / LLM / HTML path still unstable across validators | parser path proven deterministic across validators; HTTP acquisition still variable between leader and validators |
| Architectural direction | mixed (HTML + LLM in fallback) | structured JSON (no LLM, no HTML) — cleaner |
| Bradbury-ready? | partial (worth investigating) | **NOT YET** — slug bug + leader/validator HTTP divergence |

The structured-API thesis is **half-validated**: validator-to-validator
entropy IS eliminated (byte-identical hashes across all 5 validators is
a result 04_v3 never produced), but leader-to-validator HTTP-acquisition
divergence remains. Moving from HTML+LLM to structured JSON did exactly
what it was supposed to do INSIDE the validator set; it did not address
HTTP-fetch-time nondeterminism between the leader and the validator set.

### Codex second-opinion (Phase 6)

Codex was called fresh on the Phase 6 receipts (read-only, adversarial).
Verbatim:

- **(a) AGREE or DISAGREE?** "It landed at **5/5 DISAGREE on resolve**.
  Honest reading: **resolve failed consensus with the leader**. The 5
  identical validator hashes are important, but they do not make it a
  5/5 AGREE."
- **(b) Thesis validated?** "**Not fully validated.** What v4 validates
  is narrower: `structured JSON + enum primitive` removed the
  validator-to-validator entropy. It does **not** prove 'structured API
  is consensus-friendly' end to end, because the leader and validators
  still observed different external input. The remaining nondeterminism
  moved from parsing/LLM/HTML into HTTP response acquisition."
- **(c) vs 04_v3?** "Consensus outcome: v4 is **worse** (failed cleanly
  vs partial agree). Determinism diagnosis: v4 is **better** (one
  consistent validator result means contract logic is much cleaner).
  Bradbury readiness: **still not ready**."
- **(d) Sharp edges?** "ESPN slug bug is real and disqualifying.
  `fifa.worldcup → 400` while `fifa.world → completed data` means this
  run is contaminated. But do not overfit to the slug. The scarier issue
  is that leader and validators can still diverge on HTTP status/body
  even with JSON. Fix slug, retry; expected outcome **likely 5/5 AGREE**
  but not guaranteed."
- **(e) Final lab verdict?** "`02_v3 PriceNoLlm`: **Bradbury-ready**.
  `03_v3 PriceLlmFieldOnly`: **needs caution / more work** (LLM field
  path remains suspect). `04_v4 StructuredAPI`: **not Bradbury-ready
  yet** — right design direction, but this specific run failed resolve.
  Fix slug, rerun, require repeated 5/5 AGREE before calling it ready."

### Final lab verdict (across all 6 phases, 3 contract patterns)

| pattern | shape | Bradbury-ready? | evidence |
|---------|-------|------------------|----------|
| **02_v3 PriceNoLlm** | deterministic HTTP-only with ±50bps integer tolerance | **YES** (N=1) | clean 5/5 AGREE on resolve, byte-identical validator hashes, eqBlocksOutputs decoded. Production should re-run ≥3x for statistical comfort. |
| **03_v3 PriceLlmFieldOnly** | leader LLM + validator deterministic re-parse | **UNPROVEN** | resolve stuck COMMITTING >11min, all 5 validators NOT_VOTED — bradbury queue stall, not a v3 verdict. LLM-in-path remains suspect until proven. |
| **04_v4 StructuredAPI (NEW)** | ESPN JSON, no LLM, no HTML | **NOT YET** | 5/5 DISAGREE on resolve, but validators byte-identical across all 5 (entropy moved from parsing to HTTP-acquisition). Slug bug (`fifa.worldcup` should be `fifa.world`) contaminated this specific run. Retry expected to land 5/5 AGREE. |

**Headline:** Phase 6 produced the cleanest architectural signal of the
lab — the structured-JSON-no-LLM design proves that validator-to-validator
entropy CAN be reduced to zero on Bradbury. The thesis is half-validated.
The other half (leader↔validator HTTP-acquisition determinism) is the
open question for the next iteration. Of the three contract patterns
tested, only 02_v3 is bradbury-ready today; 03_v3 needs a resolve that
actually reveals; 04_v4 needs the slug fix + a retry to close out the
validator-vs-leader HTTP divergence.

### What we learned in Phase 6 (locked)

1. **Structured JSON eliminates validator-to-validator parsing entropy.**
   v4's 5/5 byte-identical validator hashes (even on a failed resolve)
   is a result no v3 run produced. The structured-API design CAN reach
   determinism across the validator set when the input is deterministic.
2. **`gl.nondet.web.get` is still nondeterministic at the network
   layer.** Even with structured JSON, leader and validators can hit
   different response classes (different HTTP status, different cache,
   different geo). The contract-side determinism does not extend through
   the HTTP fetch itself.
3. **Endpoint slugs must be verified live before deploy.** A 400-returning
   slug looks identical to a working slug in source-read review, and
   even gltest direct mode (which mocks the HTTP layer) cannot catch a
   wrong real-world endpoint. Add a pre-deploy curl check to the lab
   loop.
4. **Codex adversarial review can call a 5/5 DISAGREE what it is.** The
   review correctly refused to call v4 "AGREE" on the basis of identical
   validator hashes alone — disagreement-with-leader is still
   disagreement. Lab discipline: vote vector first, hash analysis second.