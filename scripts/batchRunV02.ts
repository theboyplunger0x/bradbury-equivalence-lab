// Batch runner: execute N sequential deploy+resolve cycles of
// 02_price_no_llm_v3 on a chosen network (bradbury OR localnet), with a
// per-run time budget, and emit structured JSON per run so downstream
// analysis can compute stats.
//
// Modeled 1:1 on batchRunV4.ts (same classifier, DV precedence, poll shape,
// summary format). Only the contract file + constructor args + default
// budget differ.
//
// Usage (from /backend):
//   node_modules/.bin/tsx scripts/batchRunV02.ts <network> <N> [budgetSeconds]
//     network        : "bradbury" | "localnet"
//     N              : integer in [1..50]
//     budgetSeconds  : optional, default 300 (per run)
//
// Example:
//   node_modules/.bin/tsx scripts/batchRunV02.ts bradbury 10 300
//   node_modules/.bin/tsx scripts/batchRunV02.ts localnet 25 120

import { readFileSync, existsSync } from "node:fs";
import { resolve as resolvePath } from "node:path";
import {
  createAccount,
  createClient,
  generatePrivateKey,
} from "genlayer-js";
import { localnet, testnetBradbury } from "genlayer-js/chains";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const WALLET_FILE = "/Users/lanzanimarcos7/.cache/genlayer-test-wallet.txt";
const BRADBURY_DIR = resolvePath(
  "/Users/lanzanimarcos7/Desktop/Proyectos/FUDmarkets/experiments/bradbury",
);
const CONTRACT_FILE = "02_price_no_llm_v3.py";

// Constructor args for both v02 and v03 price oracles: (symbol, chain).
// BTC/base = liquid pair with DexScreener coverage (same pair the v3
// smoke/legacy scripts used).
const CTOR_SYMBOL = "BTC";
const CTOR_CHAIN = "base";

// Localnet default fund: 100 GEN (in wei, 18 decimals).
const LOCALNET_FUND_WEI = 100n * 10n ** 18n;

const POLL_INTERVAL_MS = 5_000; // per task spec: poll every 5s
const DEFAULT_BUDGET_SECONDS = 300;

const DECIDED_STATES = new Set([
  "FINALIZED",
  "ACCEPTED",
  "UNDETERMINED",
  "CANCELED",
  "LEADER_ONLY_FINALIZED",
]);

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Network = "bradbury" | "localnet";

type Verdict =
  | "AGREE_SUCCESS"
  | "AGREE_ERROR"
  | "DV"
  | "CANCELED"
  | "OTHER"
  | "TIMEOUT"
  | "THROW"
  | "SKIPPED";

interface StageResult {
  hash: string;
  verdict: Verdict;
  statusName: string;
  txExecutionResultName: string;
  votes: Record<string, number>;
  contractAddress: string;
  hashesIdentical: boolean;
  elapsedMs: number;
  budgetHit: boolean;
  errorDetail: string;
}

interface RunResult {
  i: number;
  network: Network;
  deployHash: string;
  deployVerdict: Verdict;
  deployVotes: Record<string, number>;
  deployElapsedMs: number;
  resolveHash: string;
  resolveVerdict: Verdict;
  resolveVotes: Record<string, number>;
  resolveElapsedMs: number;
  totalMs: number;
  budgetHit: boolean;
  contractAddress: string;
  deployErrorDetail: string;
  resolveErrorDetail: string;
}

interface BatchSummary {
  network: Network;
  n: number;
  budgetSeconds: number;
  deployVerdicts: Record<Verdict, number>;
  resolveVerdicts: Record<Verdict, number>;
  deployElapsedMedianMs: number;
  deployElapsedP95Ms: number;
  resolveElapsedMedianMs: number;
  resolveElapsedP95Ms: number;
  totalElapsedMedianMs: number;
  totalElapsedP95Ms: number;
  budgetHitCount: number;
  startedAtIso: string;
  finishedAtIso: string;
  wallClockMs: number;
}

// ---------------------------------------------------------------------------
// Small utilities
// ---------------------------------------------------------------------------

const sleep = (ms: number): Promise<void> =>
  new Promise((r) => setTimeout(r, ms));

const isDecided = (s: unknown): boolean => {
  if (typeof s !== "string") return false;
  if (DECIDED_STATES.has(s)) return true;
  return /FINAL|ACCEPT|CANCEL|UNDETERMINED/.test(s);
};

const loadContractCode = (): string =>
  readFileSync(resolvePath(BRADBURY_DIR, CONTRACT_FILE), "utf-8");

const readBradburyPrivateKey = (): `0x${string}` => {
  if (!existsSync(WALLET_FILE)) {
    throw new Error(
      `bradbury wallet file not found: ${WALLET_FILE}. ` +
        `Fund a test wallet and drop its 0x-prefixed 32-byte private key into that file.`,
    );
  }
  const raw = readFileSync(WALLET_FILE, "utf-8").trim();
  if (!/^0x[0-9a-fA-F]{64}$/.test(raw)) {
    throw new Error("wallet file does not contain a 0x-prefixed 32-byte key");
  }
  return raw as `0x${string}`;
};

const jsonSafe = (value: unknown): string =>
  JSON.stringify(value, (_k, v) => (typeof v === "bigint" ? v.toString() : v));

// ---------------------------------------------------------------------------
// Phase 5d / 5e helpers (mirrors deployBradburyV4Worldcup.ts / batchRunV4.ts)
// ---------------------------------------------------------------------------

const aggregateVotes = (tx: unknown): Record<string, number> => {
  const out: Record<string, number> = {};
  const asObj = tx as {
    lastRound?: { validatorVotesName?: unknown };
    consensus_data?: { votes?: unknown };
  } | null;
  const votesArr = asObj?.lastRound?.validatorVotesName;
  if (Array.isArray(votesArr) && votesArr.length > 0) {
    for (const v of votesArr) {
      const key = typeof v === "string" ? v : String(v);
      out[key] = (out[key] ?? 0) + 1;
    }
    return out;
  }
  const votesObj = asObj?.consensus_data?.votes;
  if (votesObj && typeof votesObj === "object") {
    for (const v of Object.values(votesObj as Record<string, unknown>)) {
      const key = typeof v === "string" ? v : String(v);
      out[key] = (out[key] ?? 0) + 1;
    }
  }
  return out;
};

const extractContractAddress = (tx: unknown): string => {
  const t = tx as {
    recipient?: unknown;
    data?: { contract_address?: unknown };
    contract_address?: unknown;
  } | null;
  const recipient = t?.recipient;
  if (typeof recipient === "string" && recipient.startsWith("0x")) {
    return recipient;
  }
  const dataAddr = t?.data?.contract_address;
  if (typeof dataAddr === "string") return dataAddr;
  const flatAddr = t?.contract_address;
  if (typeof flatAddr === "string") return flatAddr;
  return "";
};

const validatorHashesIdentical = (tx: unknown): boolean => {
  const hashes = (tx as { lastRound?: { validatorResultHash?: unknown } })
    ?.lastRound?.validatorResultHash;
  if (!Array.isArray(hashes) || hashes.length === 0) return false;
  return hashes.every((h: unknown) => h === hashes[0]);
};

const classifyVerdict = (
  statusName: string | undefined,
  execName: string | undefined,
  votes: Record<string, number>,
): Verdict => {
  // DV-precedence: vote vector trumps status/exec labels.
  const disagreeCount = votes["DISAGREE"] ?? 0;
  const dvCount = votes["DETERMINISTIC_VIOLATION"] ?? 0;
  if (disagreeCount >= 4) return "DV";
  if (dvCount >= 4) return "DV";

  if (statusName === "CANCELED") return "CANCELED";
  if (statusName === "UNDETERMINED") return "DV";
  if (statusName === "FINALIZED" || statusName === "ACCEPTED") {
    if (execName === "SUCCESS" || execName === "FINISHED_WITH_RETURN") {
      return "AGREE_SUCCESS";
    }
    if (
      execName === "ERROR" ||
      execName === "USER_ERROR" ||
      execName === "FINISHED_WITH_ERROR"
    ) {
      return "AGREE_ERROR";
    }
  }
  return "OTHER";
};

const summarizeError = (tx: unknown): string => {
  const leader = (tx as {
    consensus_data?: { leader_receipt?: unknown[] };
  })?.consensus_data?.leader_receipt?.[0] as
    | {
        mode?: unknown;
        error?: unknown;
        genvm_result?: { stdout?: unknown; stderr?: unknown };
        genvmResult?: { stdout?: unknown; stderr?: unknown };
      }
    | undefined;
  if (!leader) return "";
  const gv = leader.genvm_result ?? leader.genvmResult ?? null;
  const compact = (s: unknown): string =>
    typeof s === "string" ? s.slice(0, 600).replace(/\s+/g, " ") : "";
  return JSON.stringify({
    mode: leader.mode ?? "",
    error: compact(leader.error),
    stdout: compact(gv?.stdout),
    stderr: compact(gv?.stderr),
  });
};

// ---------------------------------------------------------------------------
// Poll a tx hash until decided or the per-stage deadline elapses.
// ---------------------------------------------------------------------------

async function pollUntilDecidedOrBudget(
  client: unknown,
  hash: `0x${string}`,
  stageBudgetMs: number,
  label: string,
): Promise<{ tx: unknown; elapsedMs: number; budgetHit: boolean }> {
  const t0 = Date.now();
  let tx: unknown = null;
  let budgetHit = false;

  while (true) {
    const elapsed = Date.now() - t0;
    if (elapsed >= stageBudgetMs) {
      budgetHit = true;
      break;
    }
    const remaining = stageBudgetMs - elapsed;
    await sleep(Math.min(POLL_INTERVAL_MS, remaining));

    try {
      tx = await (client as { getTransaction: (a: unknown) => Promise<unknown> })
        .getTransaction({ hash });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      // eslint-disable-next-line no-console
      console.log(`[${label}] poll error: ${msg} (continuing)`);
      continue;
    }

    const t = tx as {
      statusName?: unknown;
      status?: unknown;
      txExecutionResultName?: unknown;
    } | null;
    const s = (t?.statusName ?? t?.status) as string | undefined;
    // eslint-disable-next-line no-console
    console.log(
      `[${label}] poll status=${s} exec=${t?.txExecutionResultName ?? "?"} elapsed=${
        Date.now() - t0
      }ms`,
    );
    if (isDecided(s)) break;
  }

  return { tx, elapsedMs: Date.now() - t0, budgetHit };
}

// ---------------------------------------------------------------------------
// Localnet funding helper — mirrors batchRunV4.ts.
// ---------------------------------------------------------------------------

async function fundLocalnetAccount(
  client: unknown,
  address: `0x${string}`,
  amountWei: bigint,
): Promise<void> {
  const maybeFund = (client as { fundAccount?: (a: unknown) => Promise<unknown> })
    .fundAccount;
  if (typeof maybeFund === "function") {
    try {
      await maybeFund.call(client, {
        address,
        amount: amountWei,
      });
      return;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      // eslint-disable-next-line no-console
      console.log(`[localnet] fundAccount() threw, falling back to RPC: ${msg}`);
    }
  }

  const rpcUrl = "http://127.0.0.1:4000/api";
  const body = {
    jsonrpc: "2.0",
    id: 1,
    method: "sim_fundAccount",
    params: [address, amountWei.toString()],
  };
  const resp = await fetch(rpcUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    throw new Error(
      `sim_fundAccount RPC returned ${resp.status}: ${await resp.text()}`,
    );
  }
}

// ---------------------------------------------------------------------------
// Client factory. Bradbury reuses the funded test wallet across runs;
// localnet generates + funds a fresh key per run.
// ---------------------------------------------------------------------------

interface ClientBundle {
  client: unknown;
  address: `0x${string}`;
}

async function buildClient(network: Network): Promise<ClientBundle> {
  if (network === "bradbury") {
    const pk = readBradburyPrivateKey();
    const account = createAccount(pk);
    const client = createClient({ chain: testnetBradbury, account });
    return { client, address: (account as { address: `0x${string}` }).address };
  }

  const pk = generatePrivateKey();
  const account = createAccount(pk);
  const client = createClient({ chain: localnet, account });
  const address = (account as { address: `0x${string}` }).address;
  await fundLocalnetAccount(client, address, LOCALNET_FUND_WEI);
  return { client, address };
}

// ---------------------------------------------------------------------------
// Single run: deploy + resolve of one v02 contract.
// ---------------------------------------------------------------------------

function makeSkippedStage(reason: string): StageResult {
  return {
    hash: "",
    verdict: "SKIPPED",
    statusName: "SKIPPED",
    txExecutionResultName: "SKIPPED",
    votes: {},
    contractAddress: "",
    hashesIdentical: false,
    elapsedMs: 0,
    budgetHit: false,
    errorDetail: reason,
  };
}

function summarizeStage(
  hash: string,
  tx: unknown,
  elapsedMs: number,
  budgetHit: boolean,
): StageResult {
  const t = tx as {
    statusName?: unknown;
    txExecutionResultName?: unknown;
  } | null;
  const statusName = (t?.statusName as string) ?? "UNKNOWN";
  const execName = (t?.txExecutionResultName as string) ?? "UNKNOWN";
  const votes = aggregateVotes(tx);
  const contractAddress = extractContractAddress(tx);
  const hashesIdentical = validatorHashesIdentical(tx);
  let verdict: Verdict = classifyVerdict(statusName, execName, votes);
  if (budgetHit && !isDecided(statusName)) verdict = "TIMEOUT";
  const errorDetail =
    verdict === "AGREE_ERROR" || verdict === "DV" || verdict === "OTHER"
      ? summarizeError(tx)
      : "";
  return {
    hash,
    verdict,
    statusName,
    txExecutionResultName: execName,
    votes,
    contractAddress,
    hashesIdentical,
    elapsedMs,
    budgetHit,
    errorDetail,
  };
}

async function runOnce(
  i: number,
  network: Network,
  client: unknown,
  budgetMs: number,
): Promise<RunResult> {
  const runStart = Date.now();
  const halfBudget = Math.max(POLL_INTERVAL_MS * 2, Math.floor(budgetMs / 2));
  const code = loadContractCode();

  // --- deploy ---------------------------------------------------------------
  let deployStage: StageResult;
  let deployHashStr = "";
  try {
    const deployHash = (await (
      client as { deployContract: (a: unknown) => Promise<unknown> }
    ).deployContract({
      code,
      args: [CTOR_SYMBOL, CTOR_CHAIN],
    })) as `0x${string}`;
    deployHashStr = deployHash;
    // eslint-disable-next-line no-console
    console.log(`[run ${i}] deployHash=${deployHash}`);

    const { tx, elapsedMs, budgetHit } = await pollUntilDecidedOrBudget(
      client,
      deployHash,
      halfBudget,
      `run${i}_deploy`,
    );
    deployStage = summarizeStage(deployHash, tx, elapsedMs, budgetHit);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    // eslint-disable-next-line no-console
    console.error(`[run ${i}] deploy threw: ${msg}`);
    deployStage = {
      hash: deployHashStr,
      verdict: "THROW",
      statusName: "THROW",
      txExecutionResultName: "THROW",
      votes: {},
      contractAddress: "",
      hashesIdentical: false,
      elapsedMs: Date.now() - runStart,
      budgetHit: false,
      errorDetail: msg,
    };
  }

  // --- resolve --------------------------------------------------------------
  let resolveStage: StageResult;
  const usedSoFar = Date.now() - runStart;
  const resolveBudget = Math.max(0, budgetMs - usedSoFar);

  if (
    deployStage.verdict !== "AGREE_SUCCESS" ||
    !deployStage.contractAddress
  ) {
    resolveStage = makeSkippedStage(
      `deploy verdict=${deployStage.verdict} address=${deployStage.contractAddress || "(none)"}`,
    );
  } else if (resolveBudget <= 0) {
    resolveStage = makeSkippedStage("no time budget remaining after deploy");
  } else {
    let resolveHashStr = "";
    try {
      const resolveHash = (await (
        client as { writeContract: (a: unknown) => Promise<unknown> }
      ).writeContract({
        address: deployStage.contractAddress,
        functionName: "resolve",
        args: [],
        leaderOnly: false,
      })) as `0x${string}`;
      resolveHashStr = resolveHash;
      // eslint-disable-next-line no-console
      console.log(`[run ${i}] resolveHash=${resolveHash}`);

      const { tx, elapsedMs, budgetHit } = await pollUntilDecidedOrBudget(
        client,
        resolveHash,
        resolveBudget,
        `run${i}_resolve`,
      );
      resolveStage = summarizeStage(resolveHash, tx, elapsedMs, budgetHit);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      // eslint-disable-next-line no-console
      console.error(`[run ${i}] resolve threw: ${msg}`);
      resolveStage = {
        hash: resolveHashStr,
        verdict: "THROW",
        statusName: "THROW",
        txExecutionResultName: "THROW",
        votes: {},
        contractAddress: deployStage.contractAddress,
        hashesIdentical: false,
        elapsedMs: Date.now() - (runStart + usedSoFar),
        budgetHit: false,
        errorDetail: msg,
      };
    }
  }

  const totalMs = Date.now() - runStart;

  return {
    i,
    network,
    deployHash: deployStage.hash,
    deployVerdict: deployStage.verdict,
    deployVotes: deployStage.votes,
    deployElapsedMs: deployStage.elapsedMs,
    resolveHash: resolveStage.hash,
    resolveVerdict: resolveStage.verdict,
    resolveVotes: resolveStage.votes,
    resolveElapsedMs: resolveStage.elapsedMs,
    totalMs,
    budgetHit: deployStage.budgetHit || resolveStage.budgetHit,
    contractAddress: deployStage.contractAddress,
    deployErrorDetail: deployStage.errorDetail,
    resolveErrorDetail: resolveStage.errorDetail,
  };
}

// ---------------------------------------------------------------------------
// Aggregation
// ---------------------------------------------------------------------------

const EMPTY_VERDICT_COUNTS: Record<Verdict, number> = {
  AGREE_SUCCESS: 0,
  AGREE_ERROR: 0,
  DV: 0,
  CANCELED: 0,
  OTHER: 0,
  TIMEOUT: 0,
  THROW: 0,
  SKIPPED: 0,
};

const percentile = (sortedAsc: readonly number[], p: number): number => {
  if (sortedAsc.length === 0) return 0;
  if (sortedAsc.length === 1) return sortedAsc[0]!;
  const rank = (p / 100) * (sortedAsc.length - 1);
  const lo = Math.floor(rank);
  const hi = Math.ceil(rank);
  if (lo === hi) return sortedAsc[lo]!;
  const w = rank - lo;
  return sortedAsc[lo]! * (1 - w) + sortedAsc[hi]! * w;
};

const median = (values: readonly number[]): number =>
  percentile([...values].sort((a, b) => a - b), 50);

const p95 = (values: readonly number[]): number =>
  percentile([...values].sort((a, b) => a - b), 95);

function summarize(
  network: Network,
  budgetSeconds: number,
  runs: readonly RunResult[],
  startedAtIso: string,
  wallClockMs: number,
): BatchSummary {
  const deployVerdicts: Record<Verdict, number> = { ...EMPTY_VERDICT_COUNTS };
  const resolveVerdicts: Record<Verdict, number> = { ...EMPTY_VERDICT_COUNTS };
  const deployMs: number[] = [];
  const resolveMs: number[] = [];
  const totalMs: number[] = [];
  let budgetHitCount = 0;
  for (const r of runs) {
    deployVerdicts[r.deployVerdict] += 1;
    resolveVerdicts[r.resolveVerdict] += 1;
    deployMs.push(r.deployElapsedMs);
    resolveMs.push(r.resolveElapsedMs);
    totalMs.push(r.totalMs);
    if (r.budgetHit) budgetHitCount += 1;
  }
  return {
    network,
    n: runs.length,
    budgetSeconds,
    deployVerdicts,
    resolveVerdicts,
    deployElapsedMedianMs: Math.round(median(deployMs)),
    deployElapsedP95Ms: Math.round(p95(deployMs)),
    resolveElapsedMedianMs: Math.round(median(resolveMs)),
    resolveElapsedP95Ms: Math.round(p95(resolveMs)),
    totalElapsedMedianMs: Math.round(median(totalMs)),
    totalElapsedP95Ms: Math.round(p95(totalMs)),
    budgetHitCount,
    startedAtIso,
    finishedAtIso: new Date().toISOString(),
    wallClockMs,
  };
}

// ---------------------------------------------------------------------------
// CLI entrypoint
// ---------------------------------------------------------------------------

function parseArgs(argv: readonly string[]): {
  network: Network;
  n: number;
  budgetSeconds: number;
} {
  const [rawNet, rawN, rawBudget] = argv;
  if (!rawNet || !rawN) {
    throw new Error(
      "usage: tsx scripts/batchRunV02.ts <bradbury|localnet> <N> [budgetSeconds]",
    );
  }
  if (rawNet !== "bradbury" && rawNet !== "localnet") {
    throw new Error(`unknown network "${rawNet}" (expected bradbury|localnet)`);
  }
  const n = Number.parseInt(rawN, 10);
  if (!Number.isInteger(n) || n < 1 || n > 50) {
    throw new Error(`N must be an integer in [1..50], got "${rawN}"`);
  }
  let budgetSeconds = DEFAULT_BUDGET_SECONDS;
  if (rawBudget !== undefined) {
    const b = Number.parseInt(rawBudget, 10);
    if (!Number.isInteger(b) || b < 10) {
      throw new Error(`budgetSeconds must be an integer >= 10, got "${rawBudget}"`);
    }
    budgetSeconds = b;
  }
  return { network: rawNet, n, budgetSeconds };
}

async function main(): Promise<void> {
  const { network, n, budgetSeconds } = parseArgs(process.argv.slice(2));
  const budgetMs = budgetSeconds * 1000;
  const startedAtIso = new Date().toISOString();
  const wallClockT0 = Date.now();

  // eslint-disable-next-line no-console
  console.log(
    `[batchRunV02] contract=${CONTRACT_FILE} network=${network} N=${n} budgetSeconds=${budgetSeconds} startedAt=${startedAtIso}`,
  );

  const runs: RunResult[] = [];

  for (let i = 1; i <= n; i += 1) {
    // eslint-disable-next-line no-console
    console.log(`\n===== RUN ${i}/${n} (${network}) =====`);
    let bundle: ClientBundle;
    try {
      bundle = await buildClient(network);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      // eslint-disable-next-line no-console
      console.error(`[run ${i}] client init threw: ${msg}`);
      const failed: RunResult = {
        i,
        network,
        deployHash: "",
        deployVerdict: "THROW",
        deployVotes: {},
        deployElapsedMs: 0,
        resolveHash: "",
        resolveVerdict: "SKIPPED",
        resolveVotes: {},
        resolveElapsedMs: 0,
        totalMs: 0,
        budgetHit: false,
        contractAddress: "",
        deployErrorDetail: msg,
        resolveErrorDetail: "client init failed",
      };
      runs.push(failed);
      // eslint-disable-next-line no-console
      console.log(`RUN::${jsonSafe(failed)}`);
      continue;
    }
    // eslint-disable-next-line no-console
    console.log(`[run ${i}] wallet=${bundle.address}`);

    const runResult = await runOnce(i, network, bundle.client, budgetMs);
    runs.push(runResult);
    // eslint-disable-next-line no-console
    console.log(`RUN::${jsonSafe(runResult)}`);
  }

  const summary = summarize(
    network,
    budgetSeconds,
    runs,
    startedAtIso,
    Date.now() - wallClockT0,
  );
  // eslint-disable-next-line no-console
  console.log(`\nBATCH_SUMMARY::${jsonSafe(summary)}`);
}

main().catch((e: unknown) => {
  const msg = e instanceof Error ? e.message : String(e);
  // eslint-disable-next-line no-console
  console.error("fatal:", msg);
  process.exit(1);
});
