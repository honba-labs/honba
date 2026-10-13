// Runs a small Journal v1 NDJSON fixture through the real wasm replay exports.
// Usage: node replay_conformance.mjs <wasm-pack --target nodejs pkg dir>   (see `make test-wasm`)
import { createRequire } from "node:module";
import { resolve } from "node:path";

const wasm = createRequire(import.meta.url)(resolve(process.argv[2] ?? "pkg"));

// A deterministic journal: the first line is the provenance header (`type: "header"`), the rest
// are audit records. This is exactly the layout honba-engine writes.
const header = {
  type: "header",
  schema_version: 3,
  journal_version: 1,
  seed: 7,
  config_hash: "cfg-js",
  dataset_id: "ds-js",
  trial_count: 1,
};
const records = [
  { seq: 0, type: "event_dispatched", ts_event: 1 },
  { seq: 1, type: "event_dispatched", ts_event: 2 },
  { seq: 2, type: "order_submitted", order_id: "J-1", instrument: "RELIANCE.NSE", side: "buy" },
  { seq: 3, type: "order_submitted", order_id: "J-2", instrument: "RELIANCE.NSE", side: "sell" },
  { seq: 4, type: "fill_produced", order_id: "J-1", quantity: 5, price: 2500, ts_event: 3 },
  { seq: 5, type: "fill_produced", order_id: "J-2", quantity: 2, price: 2510, ts_event: 4 },
  { seq: 6, type: "cancel_requested", order_id: "J-2" },
  { seq: 7, type: "order_lifecycle", order_id: "J-1", event: "accepted", ts_event: 5 },
  { seq: 8, type: "order_rejected", order_id: "J-9", reason: "trading_halted" },
  { seq: 9, type: "state_changed", from: "active", to: "halted" },
];
const ndjson = [header, ...records].map((o) => JSON.stringify(o)).join("\n");

let failures = 0;
const fail = (msg) => {
  failures++;
  console.error(`FAIL ${msg}`);
};
const check = (cond, msg) => {
  if (!cond) fail(msg);
};

// journal_header
const parsedHeader = JSON.parse(wasm.journal_header(ndjson));
check(parsedHeader.schema_version === 3, "header schema_version");
check(parsedHeader.journal_version === 1, "header journal_version");
check(parsedHeader.seed === 7, "header seed");
check(parsedHeader.config_hash === "cfg-js", "header config_hash");
check(parsedHeader.dataset_id === "ds-js", "header dataset_id");
check(parsedHeader.trial_count === 1, "header trial_count");

// replay_journal
const replay = JSON.parse(wasm.replay_journal(ndjson));
check(replay.header.seed === 7, "replay header seed");
const r = replay.replay;
check(r.trading_state === "halted", `trading_state ${r.trading_state}`);
check(r.dispatches === 2, `dispatches ${r.dispatches}`);

check(Array.isArray(r.positions) && r.positions.length === 1, "positions length");
check(r.positions[0]?.instrument === "RELIANCE.NSE", "position instrument");
check(r.positions[0]?.quantity === 3, "position quantity");

check(Array.isArray(r.orders) && r.orders.length === 2, "orders length");
const [o1, o2] = r.orders;
check(o1.order_id === "J-1", "order 0 id (sorted by order_id)");
check(o1.instrument === "RELIANCE.NSE", "order 0 instrument");
check(o1.side === "buy", "order 0 side");
check(o1.filled_qty === 5, "order 0 filled_qty");
check(o1.lifecycle === "accepted", "order 0 lifecycle");
check(o1.cancel_requested === false, "order 0 cancel_requested");
check(o2.order_id === "J-2", "order 1 id");
check(o2.side === "sell", "order 1 side");
check(o2.filled_qty === 2, "order 1 filled_qty");
check(o2.lifecycle === null, "order 1 lifecycle");
check(o2.cancel_requested === true, "order 1 cancel_requested");

check(r.rejections["J-9"] === "trading_halted", "rejection reason");
check(Object.keys(r.rejections).length === 1, "rejections size");

// Bad input must throw a JS Error, not return a value.
const mustThrow = (fn) => {
  try {
    fn();
  } catch (e) {
    return e instanceof Error;
  }
  return false;
};
check(mustThrow(() => wasm.replay_journal("")), "empty stream must throw");
check(mustThrow(() => wasm.journal_header("")), "empty header must throw");
check(
  mustThrow(() => wasm.journal_header('{"seq":0,"type":"event_dispatched"}')),
  "missing header must throw",
);
check(
  mustThrow(() => wasm.replay_journal(`${JSON.stringify(header)}\nnot json`)),
  "malformed record must throw",
);

if (failures) process.exit(1);
console.log("wasm replay conformance: 1 journal fixture ok (header + replay + 4 refusals)");
