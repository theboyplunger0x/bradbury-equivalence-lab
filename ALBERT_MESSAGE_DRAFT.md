# Albert message — DRAFT (do NOT send without Marcos review)

Below is the message body ready to paste. It's based on real N=30 bradbury data collected 2026-06-30 → 2026-07-01. Marcos reviews first.

---

## Draft (chat style, casual English)

```
hey — sharing some findings from experimenting with bradbury for FUD's oracle path. wanted your read.

we ran three contracts on bradbury, ~30 total deploy+resolve pairs on the same funded wallet (0x186d2dabBE79810A6F3cBD8C09033E96C767c121), each with a per-run budget of 240-420s. all logs + contracts + scripts in the public lab repo → https://github.com/theboyplunger0x/bradbury-equivalence-lab

## contract 1 — no LLM, DexScreener JSON
02_price_no_llm_v3 — parses a single int from a stable JSON API, no LLM.
N=10 on bradbury:
- deploy: 8/10 AGREE_SUCCESS, 2 TIMEOUT (5-validator committee never converged in 240s)
- resolve: 7/10 AGREE_SUCCESS, 1 TIMEOUT, 2 SKIPPED (deploy failed)
- end-to-end clean: 7/10 = 70%
- median total 28s, worst-case observed 189s (N=10 tail sample, not a real p95)
- zero DVs, zero DISAGREEs on any run

## contract 2 — structured JSON + web I/O per validator
04_worldcup_enum_v4 — parses ESPN scoreboard JSON, no LLM, each validator fetches independently.
N=15 on bradbury:
- deploy: 13/15 AGREE_SUCCESS, 2 TIMEOUT
- resolve: 8/15 AGREE_SUCCESS, 5 TIMEOUT, 2 SKIPPED
- end-to-end clean: 8/15 = 53%
- median 30s, worst-case observed 244s (N=15 tail sample, not a real p95)
- zero DVs, zero DISAGREEs. when it completes, all 5 validators produce byte-identical hashes.

## contract 3 — LLM at leader + validator re-derive
03_price_llm_field_only_v3 — LLM at leader, validators deterministically re-derive.
N=5 on bradbury:
- deploy: 1/5 AGREE_SUCCESS, 4/5 TIMEOUT
- resolve: 0/5 AGREE_SUCCESS on the one that got past deploy → 1 DV
- end-to-end clean: 0/5 = 0%
- median 214s
- first DV we've seen in ~40 total runs across the whole lab

sample tx hashes if useful:
- clean 04_v4 deploy: 0xa2c1511959884fdc68cb01316e4b56fad287d001334fce0f3c72314f5bba5591
- clean 04_v4 resolve: 0x6ddd0f7e6765e52d9c5a607689230f1bf2aaa24fce67b3caef1e33128cfef2ce
- deploy TIMEOUT 04_v4: see /tmp/batch-04v4-brad-N15.log run indices 4, 7 in the lab repo
- LLM 03 DV resolve: see /tmp/batch-03v3-brad-N5.log

**three questions:**

1. is a ~30-50% single-validator TIMEOUT rate the expected liveness profile for bradbury today, or does this look off? for FUD's UX we'd need to design around this either way, but wanted to know if we're seeing normal testnet behavior or a red flag.

2. we saw a clear step change when we added LLM — no-LLM contracts land 70% clean, LLM contract lands 0% (small N=5 but signal is stark, 4/5 deploy TIMEOUT + first DV we've observed). is `gl.eq_principle.prompt_comparative` the intended path here vs the "leader runs LLM, validators re-derive from raw output" pattern we used? we'd love a canonical example if one exists.

3. tangential upstream bug we hit while trying to set up localnet as a baseline: `yeagerai/simulator-database-migration:latest` fails to boot because its alembic.ini hardcodes `sqlalchemy.url = ...@localhost/...` instead of reading `DB_URL` from env. postgres is on the same docker network but bound to service name `postgres`, not `localhost`. reproducible with just `genlayer up --headless --numValidators 5`. want us to open an issue?

context: this is exploratory for a possible bradbury-backed oracle option in FUD (canary alongside our current studionet setup). we're not blocked on prod — production settles fine on studionet. just trying to understand the readiness curve before we spend more time integrating.
```

---

## Notes for Marcos

- **Length**: longer than the previous draft. Justified because now we have real data — Albert can act on % rates, tx hashes, and specific bug repro. Don't shorten it.
- **Tone**: "sharing findings + here's what we saw + 3 questions." Not "help us." Casual, respectful of his time.
- **Skills**: mentions `gl.eq_principle.prompt_comparative` to show we read the docs, so he doesn't send us back to skills.genlayer.com.
- **Ask #3**: the alembic bug is a real actionable finding, worth surfacing even if the main topic is liveness. Free value we can give the GenLayer team.
- **No numbers we can't defend**: every % rate above is exact from the batch logs. No rounding.

## What NOT to change without asking Marcos

- The "we're not blocked on prod" framing at the end. That protects us from being misread as "FUD is broken."
- The lab repo link. Marcos owns that repo, he needs to green-light sharing.
- The specific tx hashes. Those are auditable.

## Send channel

- Presumably GenLayer builders chat or DM to Albert directly. Marcos knows the venue.
- Do NOT paste the "Notes for Marcos" section — just the fenced Draft block above.
