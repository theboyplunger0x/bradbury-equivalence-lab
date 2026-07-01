# Stage 3 — Bradbury oracle canary integration plan

**Status**: PROPOSAL. Do NOT integrate without Marcos approval.
**Scope**: FUD backend price oracle. Bradbury 02_v3 pattern as shadow-mode canary alongside studionet (which stays primary).
**Not in scope**: worldcup oracle (04_v4 lower clean rate), LLM-based resolution (03_v3 0% clean).

## Why this contract, why now

From overnight N=30 lab data:
- 02_v3 (no LLM, DexScreener JSON): 70% end-to-end clean, zero DVs, zero DISAGREEs across 10 runs.
- Same underlying data source (DexScreener API) that our production `priceOracle` already hits from studionet. So the "source" is identical — only the consensus layer changes.
- No LLM = deterministic under normal conditions. When 5 validators complete, they produce byte-identical hashes.
- Median latency 28s. That's within the price-oracle window we already have.

The 30% failure rate makes it unfit as a PRIMARY oracle. It IS fit as a shadow-mode canary that:
- Runs in parallel with studionet
- Logs whenever it disagrees with studionet
- Never blocks user-facing settlement

## Proposed architecture

```
priceOracle request
  ├── studionet path (PRIMARY, blocking) → returns to user
  └── bradbury path (SHADOW, non-blocking)
        └── logs to `bradbury_shadow_results` table
              ├── {market_id, requested_at, studionet_price, bradbury_price, agreement, latency_ms}
              └── used for post-hoc analysis only
```

Zero user-facing risk. Zero settlement risk. Pure observability.

## Code changes (proposed minimal diff)

### New file: `backend/src/services/bradburyShadowOracle.ts` (~100 lines)

```ts
// pseudo-code / spec (not implementation)
import { createAccount, createClient } from "genlayer-js";
import { testnetBradbury } from "genlayer-js/chains";

export async function shadowResolveBradbury(
  args: { symbol: string; chain: string },
  studionetResult: { price: bigint; source: string },
): Promise<{ agreement: boolean; bradburyPrice: bigint | null; latencyMs: number; error?: string }> {
  // 1. deploy 02_v3-equivalent contract with args [symbol, chain]
  // 2. call resolve() with a hard 60s budget
  // 3. compare returned int with studionetResult.price (tolerance: 50 bps)
  // 4. return outcome — NEVER throw upstream
  //    all errors are just "agreement=false, error=<reason>" for logging
}
```

### New table: `bradbury_shadow_results` (migration, ~15 lines)

```sql
CREATE TABLE bradbury_shadow_results (
  id BIGSERIAL PRIMARY KEY,
  market_id VARCHAR(255) NOT NULL,
  requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  symbol VARCHAR(50) NOT NULL,
  chain VARCHAR(50) NOT NULL,
  studionet_price NUMERIC NOT NULL,
  bradbury_price NUMERIC,
  agreement BOOLEAN NOT NULL DEFAULT FALSE,
  latency_ms INTEGER NOT NULL,
  bradbury_error TEXT,
  bradbury_verdict VARCHAR(30),  -- AGREE_SUCCESS / TIMEOUT / DV / OTHER
  CONSTRAINT fk_market FOREIGN KEY (market_id) REFERENCES markets(id)
);
CREATE INDEX idx_bradbury_shadow_agreement ON bradbury_shadow_results(agreement);
CREATE INDEX idx_bradbury_shadow_requested_at ON bradbury_shadow_results(requested_at DESC);
```

### Call-site change: `backend/src/services/priceOracle.ts`

Add ONE line after the studionet result returns:

```ts
const studionetResult = await callStudionetOracle(args);
// existing code returns studionetResult to caller

// new: fire-and-forget shadow call (never awaited by the caller path)
if (process.env.BRADBURY_SHADOW_ENABLED === "true") {
  shadowResolveBradbury(args, studionetResult).then(shadowResult => {
    return db.query(
      "INSERT INTO bradbury_shadow_results (market_id, symbol, chain, studionet_price, bradbury_price, agreement, latency_ms, bradbury_error, bradbury_verdict) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
      [args.marketId, args.symbol, args.chain, studionetResult.price, shadowResult.bradburyPrice, shadowResult.agreement, shadowResult.latencyMs, shadowResult.error ?? null, shadowResult.verdict]
    );
  }).catch(() => { /* swallow — shadow is never allowed to break prod */ });
}

return studionetResult;
```

## Rollout plan

Phase A — build (behind `BRADBURY_SHADOW_ENABLED` env flag, default off):
1. Deploy the new service + migration to backend
2. Flag stays OFF in prod → no shadow calls fire → zero risk
3. Merge to main, deploy backend

Phase B — canary flag ON (dev/staging):
4. Flip flag ON in staging for 1 week
5. Watch `bradbury_shadow_results` for: agreement rate, latency distribution, error patterns
6. Success criteria: >=60% agreement + latency p95 < 60s

Phase C — canary flag ON (production, invisible):
7. Flip flag ON in prod
8. Watch same metrics but with real traffic
9. Success criteria: same as B + no shadow-triggered incidents

Phase D — future (NOT in this plan):
- If shadow data is trustworthy after 4 weeks of prod, discuss promotion to co-primary (dual-oracle with majority rule) — but that's a separate proposal.

## Explicit non-goals

- Do NOT replace studionet as primary
- Do NOT block user-facing paths on bradbury
- Do NOT alert on shadow disagreement in initial phase (too noisy given 30% failure)
- Do NOT integrate 04_v4 or 03_v3 patterns — only 02_v3
- Do NOT integrate on mainnet until Phase C runs clean for 2+ weeks

## Estimated effort

- Backend implementation: ~4h (new service + migration + call-site tweak + tests)
- Testing: ~2h
- Deployment: ~30min
- Total: ~1 day

## Risks

- Cost: each shadow call spends ~0.5 GEN on bradbury. If FUD does 1000 price fetches/day → 500 GEN/day. That's real money. Need funded wallet or budget cap.
- Wallet key management: shadow wallet PK needs to live somewhere (env var). Rotation policy needed.
- Log volume: 1000 rows/day is fine, but if we scale to 100k/day, need retention policy.
- False confidence: 70% agreement doesn't mean the 30% failures are safe. Shadow data must NEVER influence settlement decisions.

## What we need from Marcos

1. Green-light the shadow architecture (or reject / redirect)
2. Approve budget for shadow wallet funding (~500 GEN/day per above)
3. Confirm log retention (30 days? 90? forever?)
4. Confirm rollout timing — post-worldcup? this month? Q3?
