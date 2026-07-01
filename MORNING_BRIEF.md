# MORNING BRIEF — 2026-07-01

**Status**: overnight completed. Real N=30 data across 3 contracts on bradbury. Localnet blocked by upstream bug (NOT lab-fixable).

## TL;DR

- **02_v3** (no LLM, DexScreener JSON): **70%** end-to-end clean → closest to prod-ready
- **04_v4** (structured JSON + web I/O per validator): **53%** clean → web I/O adds variance vs pure JSON
- **03_v3** (WITH LLM at leader + validator re-derive): **0%** clean, 4/5 deploy TIMEOUT + first DV seen on resolve → LLM path unsuitable for bradbury in current form
- Localnet baseline UNAVAILABLE: `yeagerai/simulator-database-migration:latest` has hardcoded `localhost` in alembic.ini instead of reading `DB_URL` env — real upstream bug worth reporting

## Data (bradbury only, N=30 total)

| Contract | N | Deploy AGREE | Resolve AGREE | End-to-end clean | Median total | Tail sample (max) |
|---|---|---|---|---|---|---|
| 02_v3 (no LLM) | 10 | 8/10 (80%) | 7/10 (70%) | **7/10 (70%)** | 28s | 189s |
| 04_v4 (JSON + web) | 15 | 13/15 (87%) | 8/15 (53%) | **8/15 (53%)** | 30s | 244s |
| 03_v3 (LLM) | 5 | 1/5 (20%) | 0/5 (+1 DV) | **0/5 (0%)** | 214s | 214s |

> N here is intentionally small — treat the "tail sample" column as tail sampling, not a statistically valid p95 / SLA claim. It's the observed max within each batch, useful as a "worst-case we saw" anchor, not a distribution percentile.

Detailed BATCH_SUMMARY JSON in `/tmp/batch-{04v4-brad-N15,02v3-brad-N10,03v3-brad-N5}.log`.

## Key findings

1. **LLM MATTERS for bradbury liveness.** 02 (no LLM) 70% vs 03 (LLM) 0%. This contradicts my earlier hypothesis "LLM isn't the culprit, it's just liveness." With LLM, 4/5 deploys TIMEOUT and the one that got through hit DV on resolve.
2. **Even no-LLM contracts have ~30-50% failure on bradbury.** Not catastrophic but not production-grade without retry logic.
3. **Web I/O adds ~15pp variance.** 04 (JSON + web I/O per validator) is 53% vs 02 (JSON only) 70%. Suggests fetching data during resolve adds some entropy.
4. **First DV observed in resolve stage** on the one 03_v3 that got past deploy. LLM validators produced different result hashes — validator re-derivation of the leader's LLM output failed to converge.
5. **Tail-sample latencies are alarming**: 189s / 244s / 214s (observed max per batch — small-N tail sampling, not a true p95). If anything close to these lands on the price-oracle path in prod, we lose the "2-minute settlement" UX.

## Overnight timeline (actual)

1. 22:00 — launched Phase 8 (localnet + bradbury comparative). Phase 8 boot failed at migration container.
2. 22:20 — overnight autonomous workflow prematurely aborted on wait-agent (didn't actually wait 90 min).
3. 22:30 — root-caused Phase 8 failure: alembic.ini regression in `yeagerai/simulator-database-migration:latest`.
4. 22:40 — pivot to bradbury-only 3-contract batch (localnet unrecoverable without image surgery).
5. 22:41-02:40 — chain script sequentially ran 04_v4 N=15, then 02_v3 N=10, then 03_v3 N=5. Nonce race prevents parallel runs on a single wallet.
6. 02:40 — all 3 batches complete. Total GEN spent: ~30 GEN (wallet still has ~88 GEN).

## Decisions needed from Marcos

1. **Localnet upstream bug** — report to GenLayer team? This is a real actionable finding independent of any consensus questions. Reproducible: `genlayer up --headless --numValidators 5` → migration container exits 1 with `psycopg2.OperationalError: connection to server at "localhost" port 5432`. Ready-to-send bug report snippet in `ALBERT_MESSAGE_DRAFT.md`.

2. **Albert message on liveness data** — `ALBERT_MESSAGE_DRAFT.md` (this same dir) has a draft with specific N=30 % rates + tx hashes + 3 specific questions. Codex verdict: YES ready to send after Marcos review.

3. **Stage 3 canary go/no-go** — `STAGE3_INTEGRATION_PLAN.md` proposes 02_v3 as shadow-mode canary alongside studionet for price oracles. Design assumes retry logic (30% failure needs to be masked). Marcos reviews.

4. **03_v3 LLM path — kill or fix?** 0% clean is disqualifying in current form. Options:
   - Drop LLM entirely from FUD bradbury roadmap
   - Redesign with `gl.eq_principle.prompt_comparative` (validators compare LLM outputs instead of re-derive)
   - Wait for GenLayer to improve LLM stability

## Blockers / open questions

- Localnet baseline missing → can't attribute liveness variance to bradbury infra vs protocol vs contract-shape with certainty. Current data is bradbury-only.
- N=5 for LLM contract is small. Would need N=15+ to be statistically confident. But 0/5 with 20% DV is a strong signal already.
- No comparison against studionet baseline (which is what FUD prod uses). Would strengthen any Albert conversation.

## Wallet + costs

- Wallet: `0x186d2dabBE79810A6F3cBD8C09033E96C767c121`
- Bradbury balance before overnight: ~119 GEN
- Approx spent: ~30 GEN (~1 GEN per deploy+resolve pair, more for LLM)
- Balance after: ~88 GEN (safe buffer for follow-up)

## Framing note (mandatory)

All findings above are **LAB / experimental**. Production WC settle uses studionet via `worldcupMatchAutoSettler.ts` — nothing here changes production behavior. Do NOT frame these findings as "FUD prod has a liveness bug" — that would be wrong.
