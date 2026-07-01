import { readFileSync } from "node:fs";
import { createAccount, createClient } from "genlayer-js";
import { testnetBradbury } from "genlayer-js/chains";

const WALLET_FILE = "/Users/lanzanimarcos7/.cache/genlayer-test-wallet.txt";

// Phase 10 05_v1 batch — the 2 runs that produced RESOLVE_CONSENSUS_REVERT
// AND did NOT recover on retry. Sourced from /tmp/batch-05v1-brad-N10-retry.log.
const CONTRACTS = [
  {
    run: 6,
    address: "0xcd21d4A494b3a02f9e87B014F9876C1ec216AB7d",
    deploy_tx:
      "0xa32743a6b1327344e366cda4b20d930b6e4b1289bf7753ca79d37cab19e7e5a4",
  },
  {
    run: 7,
    address: "0x687d56126c6dd112BeeC56fE6AEdAdA55514371A",
    deploy_tx:
      "0x610e6ab843e46a48ecdcd67e473addd6464bf5c5d584b652aef081124aeabd4a",
  },
] as const;

type InspectRow = {
  run: number;
  address: string;
  exit_price: string;
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

async function inspectContract(client: any, item: (typeof CONTRACTS)[number]) {
  const notes: string[] = [];

  // 05_v1 exposes @gl.public.view def get_price(self) -> str
  // The init default is exit_price = "0"; resolve() sets it to the LLM value.
  const priceRead = await readMethod(client, item.address, "get_price");
  let exitPrice = "";
  let resolveExecuted = false;

  if (priceRead.ok) {
    exitPrice = asString(priceRead.value).trim();
    notes.push(`get_price returned: ${JSON.stringify(priceRead.value, bigintReplacer)}`);

    // Default from __init__ is "0". Any other non-empty value means resolve()
    // executed and mutated state (regardless of whether the wrapper tx later
    // reverted). An empty string would also be unexpected — treat as default.
    if (exitPrice !== "" && exitPrice !== "0") {
      resolveExecuted = true;
      notes.push("exit_price differs from init default '0' → resolve executed");
    } else {
      notes.push("exit_price is init default '0' → resolve NEVER executed");
    }
  } else {
    notes.push(`get_price error: ${priceRead.error}`);
    notes.push("view read failed; resolve_executed=false is not state evidence");
  }

  return {
    run: item.run,
    address: item.address,
    exit_price: exitPrice,
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
    row.notes.some((note) => note.startsWith("view read failed")),
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
