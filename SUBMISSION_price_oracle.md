# PriceNoLlm — consensus on a value that changes while you read it

A deterministic, no-LLM GenLayer Intelligent Contract that resolves a USD token
price from a public DEX aggregator.

**Source:** [`02_price_no_llm_v4.py`](./02_price_no_llm_v4.py) (single file, no
sibling imports, pinned runner)

---

## The problem this primitive actually solves

Reading a price is not the interesting part. The interesting part is this:

> A live DEX price **legitimately changes** between the moment the leader reads
> it and the moment each validator re-reads it, seconds later.

That breaks the two obvious approaches:

- **Exact-equality consensus is impossible.** The leader sees `0.00421337`, a
  validator two seconds later sees `0.00421340`. Both are correct. Byte equality
  would make every honest run fail.
- **"Close enough" in floating point reintroduces non-determinism.** Float
  rounding is platform- and order-dependent, so two validators can disagree
  about whether the same two numbers are within tolerance.

So the primitive is: **a tolerance band expressed in basis points and evaluated
in pure integer arithmetic**, paired with a validator that independently
re-derives the value instead of rubber-stamping the leader's.

This generalizes to any oracle over a continuously-moving quantity (prices,
rates, temperatures, load metrics), which is why it is worth having as a
reference rather than as a demo.

---

## How consensus is used

`resolve()` runs `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)` with a
**custom validator**, not a default equality check.

**Leader** (`leader_fn`)
1. `GET https://api.dexscreener.com/latest/dex/search?q={symbol}`
2. Filter pairs by `chainId` and `baseToken.symbol`
3. Deterministically select the pair with the **highest `liquidity.usd`**
4. Parse `priceUsd` into integer fixed-point (`price * 1e9`)
5. Return it as a **bare primitive string** — never a dict

**Validator** (`validator_fn`)
1. Re-fetch the same endpoint **itself**
2. Re-run the *same* selection and parsing
3. Compare its own value against the leader's with
   `_within_int(leader, mine, TOLERANCE_BPS)`
4. AGREE only if they fall inside the band

The validator never accepts the leader's number as input to its own derivation.
It computes its own answer and only then compares. That is the difference
between independent verification and coordinated rubber-stamping.

### The tolerance band, in integers

```python
# diff / ((a + b) / 2)  <=  tol_bps / 10_000
# rewritten with no division and no floats:
return diff * 20_000 <= (a + b) * tol_bps
```

`TOLERANCE_BPS = 50` (0.5%). Wide enough to absorb intra-block DEX movement
between the leader's fetch and a validator's fetch; tight enough that a
different pair, a stale quote, or a manipulated print still falls outside.

### Failure reconciliation (the part most demos skip)

A naive contract treats *any* leader failure as a disagreement, so one transient
503 burns a consensus round. This contract tags every error with a determinism
class and reconciles accordingly:

| Prefix | Meaning | Validator rule |
|---|---|---|
| `[EXPECTED]` | contract's own business-logic error | agree only on a **byte-equal** message |
| `[EXTERNAL]` | upstream returned a deterministic 4xx | agree only on a **byte-equal** message |
| `[TRANSIENT]` | network failure / upstream 5xx | agree if **both sides** independently hit a transient |
| `[LLM_ERROR]` | unparseable LLM output | **always disagree**, forcing leader rotation |

The rule follows from determinism: identical inputs must produce an identical
deterministic error, so those require byte equality. A transient is by
definition not reproducible, so requiring byte equality there would guarantee
failure.

---

## Design decisions, and why each one exists

| Decision | Reason |
|---|---|
| **No LLM** | The derivation is fully specified. An LLM here adds latency and non-determinism to a problem that has an exact answer. Our own N=20 measurement put the LLM variant at ~25% minority-DV and ~5x latency versus the deterministic one. |
| **Integer fixed-point everywhere** (`price * 1e9`) | A bare float makes the value platform-rounding dependent and therefore consensus-hostile. Decimal strings are parsed digit-by-digit into integers; no `float()` anywhere. |
| **Leader returns a bare string** | Returning a dict puts JSON key ordering and serialization variance into the consensus hash. Advisory formatting lives in the `@gl.public.view`, which never participates in consensus. |
| **Deterministic pair selection** | "Highest `liquidity.usd` among matching pairs" is a total order every validator computes identically from the same payload. Anything ambiguous (first match, arbitrary iteration) would split validators. |
| **Single self-contained file** | Contracts run in a per-validator sandbox with no access to sibling modules. `from _genlayer_helpers import ...` raises ImportError on every validator and the deploy returns `FINISHED_WITH_ERROR`. Helpers are inlined, with a comment saying why. |
| **Pinned runner** | `py-genlayer:latest` / `:test` are rejected. The header pins a content-addressed runner hash. |
| **Latch only on a terminal result** | See the v4 changelog below. |

---

## Evidence

This contract has a measured lineage on the GenLayer **bradbury** testnet, not
just a local test. Every hash below is verifiable on-chain.

**v4 (this submission) — 5/5 AGREE on both deploy and resolve**

```
deploy    0xc9e276265ad4113393b61618f688354b9a1c75256174f9b92c961576376caa7d
          ACCEPTED / FINISHED_WITH_RETURN, votes {AGREE: 5}
contract  0x04579036e405552b4c124D98F35e043A8c8fa6f7

resolve   0x3156b3ca49d1b7795a84b45c2182a1960df73dee94f723d6b4f05a4ecc3e4ece
          ACCEPTED / FINISHED_WITH_RETURN
          resultName        AGREE
          validatorVotes    [1, 1, 1, 1, 1]
          votesCommitted    5
          votesRevealed     5
```

Five validators independently re-fetched the price, re-derived it, compared
against the leader's value through the tolerance band, and all five agreed.

**v3 (previous version) — also 5/5, kept for the lineage**

```
tx        0xcfa9cc2353be5a8d967a6d6222e32fb11d191efb95c27c226db77c91a2d20720
          ACCEPTED / FINISHED_WITH_RETURN, votes {AGREE: 5}
contract  0x6f3784b61c6539a36B51F93ABEcD8bb7B01592e0
```

An earlier run in the same lineage is recorded too, with `{DISAGREE: 5}` /
`FINISHED_WITH_ERROR` (tx
`0xa9405b9a9bf8ee714498de908502bdcd3a5993da1b126d01274862d7954764fb`). All three
are kept on purpose: the failure, the diagnosis, and the fix are the useful part
for anyone building the same shape of oracle.

### Field note: do not call `resolve()` immediately after deploy

The v4 run reverted the first time, at the EVM level, 2.2 seconds after a
successful `{AGREE: 5}` deploy:

```
Transaction reverted: EVM tx 0xe19e3052... to consensus contract
0x0112Bf6e83497965A5fdD6Dad1E447a6E004271D was reverted.
```

The identical call against the identical contract address succeeded once the
deploy had settled. If your harness deploys and resolves back-to-back you will
see a revert that has nothing to do with your contract logic. Let the deploy
settle first, then submit the write.

Related tooling notes for anyone reproducing this:

- Use `client.getTransaction({ hash })`. `getTransactionReceipt` is EVM-only and
  reports "not found" for GenLayer transactions.
- Do not `waitForTransactionReceipt`; the receipt indexer can lag. Submit, then
  poll `getTransaction`.
- Votes live at `tx.lastRound.validatorVotes`, with `votesCommitted` /
  `votesRevealed` alongside; the verdict is `tx.resultName` and the execution
  outcome is `tx.txExecutionResultName`.
- A transient `status: 14` during polling is not an error. Keep polling.

---

## What changed in v4, and why it matters

Both v4 changes came out of an adversarial review *after* v3's successful run.
Passing consensus does not mean the contract is correct.

**1. The terminal-latch bug.** v3 ended `resolve()` with an unconditional
`self.resolved = True`, while `resolve()` opens with
`if self.resolved: raise "already resolved"`. So a run where consensus produced
**no usable price** still latched: `price_micro_usd` was pinned to a non-price
and no later call could ever repair it. **One transient upstream failure bricked
the oracle permanently.** v4 latches only on a parseable positive price; a failed
run leaves the contract resolvable again.

This is not hypothetical. We hit the same shape in production on a sibling
sports oracle: it answered "I don't know", latched that answer, and could never
learn the real result.

**2. `errors="ignore"` on UTF-8 decode.** v3 decoded the response body with
`errors="ignore"`, which silently discards invalid bytes. Two validators handed
different bytes could therefore derive different text — and agree on a value
neither of them actually read. v4 decodes strictly; a decode failure is
deterministic for identical bytes and reconciles cleanly as `[EXTERNAL]`.

---

## Limitations (please read before reusing)

- **Single source.** It reads one aggregator. It detects leader/validator
  divergence; it does **not** detect the aggregator itself being wrong or
  manipulated. Cross-source agreement is a different, stronger design.
- **Not manipulation-proof.** A pool attack that moves the real quote moves it
  for every validator equally, so they will agree on a manipulated price. The
  tolerance band catches divergence, not dishonesty.
- **Liquidity-max selection is a policy, not a truth.** It is deterministic and
  reasonable, not canonical.
- **The band is a tuning parameter.** 50 bps suits liquid pairs. Illiquid or
  highly volatile tokens will need a different value, and widening it to make
  runs pass trades away exactly the detection you wanted.

---

## Running it

```bash
# 1. lint (plugin tool) — forbidden imports, runner pin, decorators
genvm-lint check 02_price_no_llm_v4.py

# 2. local logic against mocked inputs (does NOT prove consensus)
pytest tests/integration/

# 3. real leader + validators
gltest --network localnet

# 4. bradbury testnet: deploy + resolve, confirm votes
```

Consensus is only actually exercised from step 3 onward. Steps 1-2 passing while
step 4 fails is the normal experience, and is why the receipts above matter more
than the unit tests. Remember to let the deploy settle before calling `resolve()`
(see the field note above).

Constructor: `PriceNoLlm(symbol: str, chain: str)` — e.g. `("WETH", "base")`.
`resolve()` performs the consensus run; `get_price()` is a read-only view
returning both the integer and a formatted decimal string.
