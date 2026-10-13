// Runs schema/conformance/screener_scan.json through the real wasm screener exports.
// Usage: node screener_conformance.mjs <wasm-pack --target nodejs pkg dir>   (see `make test-wasm`)
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const wasm = createRequire(import.meta.url)(resolve(process.argv[2] ?? "pkg"));
const fx = JSON.parse(
  readFileSync(resolve(here, "../../../../schema/conformance/screener_scan.json"), "utf8"),
);
const rel = fx.tolerance.relative;

const closeEnough = (a, e) =>
  Number.isInteger(e) ? a === e : Math.abs(a - e) <= rel * Math.max(1, Math.abs(e));

const scan = (which, doc, input) => {
  const o = Float64Array.from(input.open);
  const h = Float64Array.from(input.high);
  const l = Float64Array.from(input.low);
  const c = Float64Array.from(input.close);
  const v = Float64Array.from(input.volume);
  const call = which === "predicate" ? wasm.screener_scan_predicate : wasm.screener_scan_group;
  return JSON.parse(call(doc, o, h, l, c, v));
};

const mustThrow = (which, doc, input) => {
  try {
    scan(which, doc, input);
  } catch (e) {
    return e instanceof Error;
  }
  return false;
};

let failures = 0;
const fail = (msg) => {
  failures++;
  console.error(`FAIL ${msg}`);
};

for (const c of fx.cases) {
  const input = fx.inputs[c.input];
  const doc = JSON.stringify(c.predicate);
  if (c.expected.error !== undefined) {
    if (!mustThrow("predicate", doc, input)) {
      fail(`${c.name} must throw an Error (python raised ${c.expected.error})`);
    }
    continue;
  }
  let result;
  try {
    result = scan("predicate", doc, input);
  } catch (e) {
    fail(`${c.name}: ${e}`);
    continue;
  }
  if (result.result !== c.expected.result) {
    fail(`${c.name} result ${result.result} != ${c.expected.result}`);
  }
  for (const [key, want] of Object.entries(c.expected.metrics ?? {})) {
    const have = result.metrics[key];
    if (want === null) {
      if (have !== null) fail(`${c.name} metric ${key}: got ${have}, want null`);
    } else if (typeof have !== "number" || !closeEnough(have, want)) {
      fail(`${c.name} metric ${key}: got ${have}, want ${want}`);
    }
  }
}

for (const c of fx.groups) {
  let result;
  try {
    result = scan("group", JSON.stringify(c.group), fx.inputs[c.input]);
  } catch (e) {
    fail(`${c.name}: ${e}`);
    continue;
  }
  if (result.result !== c.expected.result) {
    fail(`${c.name} result ${result.result} != ${c.expected.result}`);
  }
}

for (const c of fx.unsupported) {
  if (!mustThrow("predicate", JSON.stringify(c.predicate), fx.inputs[c.input])) {
    fail(`unsupported ${c.name} must throw an Error`);
  }
}

for (const c of fx.divergences) {
  let result;
  try {
    result = scan("predicate", JSON.stringify(c.predicate), fx.inputs[c.input]);
  } catch (e) {
    fail(`divergence ${c.name}: ${e}`);
    continue;
  }
  if (result.result !== c.rust.result) {
    fail(`divergence ${c.name} result ${result.result} != ${c.rust.result}`);
  }
}

if (failures) process.exit(1);
console.log(
  `wasm screener conformance: ${fx.cases.length} predicate vectors ok, ` +
    `${fx.groups.length} group vectors ok, ${fx.unsupported.length} unsupported rejected, ` +
    `${fx.divergences.length} divergences ok`,
);
