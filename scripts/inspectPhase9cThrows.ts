import { readFileSync } from "node:fs";
import { createAccount, createClient } from "genlayer-js";
import { testnetBradbury } from "genlayer-js/chains";

const WALLET_FILE = "/Users/lanzanimarcos7/.cache/genlayer-test-wallet.txt";

const CONTRACTS = [
  {
    run: 2,
    address: "0x96Dd14C7d4ABA8cCF9D70E67a433Caa70ed77fBC",
    deploy_tx:
      "0x42a5666053bab351cb64a1b225a938987369269e864e1d8aab5afcd9505eeb38",
  },
  {
    run: 7,
    address: "0x3988cd766B61c42850f4D30F6E6Ab1382A51C649",
    deploy_tx:
      "0xb49df9306e5f7145e0013c1be6e1dd656ea2444088425088264cc7ee0bd65aea",
  },
  {
    run: 10,
    address: "0xDc95136266ef807AB4eC4a5B5E8eC7F40Fc5a23E",
    deploy_tx:
      "0xebff1831d517ab3dcd0c6e1a4be2a5ca130d2365dbadc8f994f29d787b354c24",
  },
] as const;

type InspectRow = {
  run: number;
  address: string;
  outcome: string;
  score: string;
  home_away_csv: string;
  resolve_executed: boolean;
  notes: string[];
};

const bigintReplacer = (_key: string, value: unknown) =>
  typeof value === "bigint" ? value.toString() : value;

const readPrivateKey = (): `0x${string}` => {
  const raw = readFileSync(WALLET_FILE, "utf-8").trim();
  if (!/^0x[0-9a-fA-F]{64}$/.test(raw)) {
    throw new Error(`${WALLET_FILE} does not contain a 0x-prefixed 32-byte key`);
  }
  return raw as `0x${string}`;
};

const errMessage = (error: unknown): string => {
  const message =
    error instanceof Error ? error.message : JSON.stringify(error, bigintReplacer);
  return String(message ?? error).replace(/\s+/g, " ").slice(0, 220);
};

const asRecord = (value: unknown): Record<string, unknown> | null => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
};

const asString = (value: unknown): string => {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (typeof value === "bigint") return value.toString();
  if (value == null) return "";
  return JSON.stringify(value, bigintReplacer);
};

const readMethod = async (
  client: any,
  address: string,
  functionName: string,
): Promise<{ ok: true; value: unknown } | { ok: false; error: string }> => {
  try {
    const value = await client.readContract({
      address,
      functionName,
      args: [],
    });
    return { ok: true, value };
  } catch (error) {
    return { ok: false, error: errMessage(error) };
  }
};

const isNonDefaultOutcome = (outcome: string): boolean => {
  const normalized = outcome.trim().toUpperCase();
  return (
    normalized === "TEAM_A_WIN" ||
    normalized === "TEAM_B_WIN" ||
    normalized === "DRAW"
  );
};

async function inspectContract(client: any, item: (typeof CONTRACTS)[number]) {
  const notes: string[] = [];

  const outcomeRead = await readMethod(client, item.address, "get_outcome");
  let outcome = "";
  let score = "";
  let homeAwayCsv = "";
  let resolved: boolean | null = null;
  let successfulReads = 0;

  if (outcomeRead.ok) {
    successfulReads += 1;
    const state = asRecord(outcomeRead.value);
    if (state) {
      outcome = asString(state.outcome);
      score = asString(state.score);
      homeAwayCsv = asString(state.home_away_csv);
      if (typeof state.resolved === "boolean") {
        resolved = state.resolved;
      }
      notes.push("get_outcome returned state object");
    } else {
      outcome = asString(outcomeRead.value);
      notes.push("get_outcome returned primitive value");
    }
  } else {
    notes.push(`get_outcome error: ${outcomeRead.error}`);
  }

  const scoreRead = await readMethod(client, item.address, "get_score");
  if (scoreRead.ok) {
    successfulReads += 1;
    const directScore = asString(scoreRead.value);
    if (directScore) score = directScore;
    notes.push("get_score direct read succeeded");
  } else {
    notes.push(`get_score unavailable/error: ${scoreRead.error}`);
  }

  const homeAwayRead = await readMethod(client, item.address, "get_home_away_csv");
  if (homeAwayRead.ok) {
    successfulReads += 1;
    const directHomeAway = asString(homeAwayRead.value);
    if (directHomeAway) homeAwayCsv = directHomeAway;
    notes.push("get_home_away_csv direct read succeeded");
  } else {
    notes.push(`get_home_away_csv unavailable/error: ${homeAwayRead.error}`);
  }

  const hasMutatedState =
    resolved === true ||
    isNonDefaultOutcome(outcome) ||
    score.trim().length > 0 ||
    homeAwayCsv.trim().length > 0;
  const resolveExecuted = successfulReads > 0 && hasMutatedState;

  if (successfulReads === 0) {
    notes.push(
      "all view reads failed; resolve_executed=false is not state evidence",
    );
  } else if (resolved === false && !resolveExecuted) {
    notes.push(
      "resolved=false with default/blank state; outcome UNKNOWN is init default",
    );
  } else if (resolved === true) {
    notes.push("resolved=true in contract state");
  } else {
    notes.push("resolve_executed inferred from non-default persisted fields");
  }

  return {
    run: item.run,
    address: item.address,
    outcome,
    score,
    home_away_csv: homeAwayCsv,
    resolve_executed: resolveExecuted,
    notes,
  } satisfies InspectRow;
}

async function main() {
  const account = createAccount(readPrivateKey());
  const client = createClient({ chain: testnetBradbury, account });
  const rows: InspectRow[] = [];

  for (const item of CONTRACTS) {
    const row = await inspectContract(client as any, item);
    rows.push(row);
    console.log(JSON.stringify(row, bigintReplacer));
  }

  const unknown = rows.filter((row) =>
    row.notes.some((note) => note.startsWith("all view reads failed")),
  ).length;
  const executed = rows.filter((row) => row.resolve_executed).length;
  const notExecuted = rows.length - executed - unknown;
  console.log(
    JSON.stringify(
      {
        aggregate: {
          total: rows.length,
          resolve_executed_true: executed,
          resolve_executed_false: notExecuted,
          resolve_executed_unknown: unknown,
        },
      },
      bigintReplacer,
    ),
  );

  if (unknown > 0) {
    process.exitCode = 2;
  }
}

main().catch((error) => {
  console.error(JSON.stringify({ fatal: errMessage(error) }));
  process.exit(1);
});
