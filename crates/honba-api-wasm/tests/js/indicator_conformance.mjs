// Runs schema/conformance/indicator_series.json through the real wasm `indicator_series` export.
// Usage: node indicator_conformance.mjs <wasm-pack --target nodejs pkg dir>   (see `make test-wasm`)
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const wasm = createRequire(import.meta.url)(resolve(process.argv[2] ?? "pkg"));
const fx = JSON.parse(
  readFileSync(resolve(here, "../../../../schema/conformance/indicator_series.json"), "utf8"),
);
const rel = fx.tolerance.relative;

const ok = (a, e) =>
  e === null
    ? Number.isNaN(a)
    : Number.isInteger(e)
      ? a === e
      : Math.abs(a - e) <= rel * Math.max(1, Math.abs(e));

let failures = 0;
for (const c of fx.cases) {
  const out = wasm.indicator_series(c.indicator, JSON.stringify(c.params), Float64Array.from(fx.inputs[c.input]));
  const bad = out.length !== c.expected.length || [...out].findIndex((a, i) => !ok(a, c.expected[i]));
  if (bad !== false && bad !== -1) {
    failures++;
    console.error(`FAIL ${c.name} (first mismatch at ${bad})`);
  }
}
const names = JSON.parse(wasm.list_indicators()).indicators.map((i) => i.name);
if (names.join() !== "sma,ema,rsi,macd,bollinger,atr") { failures++; console.error("FAIL catalog", names); }
let threw = false;
try { wasm.indicator_series("sma", '{"period":0}', new Float64Array([1])); } catch { threw = true; }
if (!threw) { failures++; console.error("FAIL bad params must throw"); }
threw = false;
try { wasm.indicator_series("sma", '{"period":2}', new Float64Array([1e308, 1e308, 1e308])); } catch { threw = true; }
if (!threw) { failures++; console.error("FAIL overflow must throw"); }

// OHLC indicators (ohlc_series.json) through ohlc_indicator_series.
const ofx = JSON.parse(
  readFileSync(resolve(here, "../../../../schema/conformance/ohlc_series.json"), "utf8"),
);
for (const c of ofx.cases) {
  const b = ofx.inputs[c.input];
  const out = wasm.ohlc_indicator_series(
    c.indicator, JSON.stringify(c.params),
    Float64Array.from(b.high), Float64Array.from(b.low), Float64Array.from(b.close),
  );
  const bad = out.length !== c.expected.length || [...out].findIndex((a, i) => !ok(a, c.expected[i]));
  if (bad !== false && bad !== -1) {
    failures++;
    console.error(`FAIL ${c.name} (first mismatch at ${bad})`);
  }
}
const f = (...a) => Float64Array.from(a);
for (const [what, args] of [
  ["unequal lengths", ["atr", '{"period":2}', f(2, 3), f(1), f(1.5, 2)]],
  ["bad period", ["atr", '{"period":0}', f(2), f(1), f(1.5)]],
  ["fractional period", ["atr", '{"period":2.5}', f(2), f(1), f(1.5)]],
  ["huge period", ["atr", '{"period":1000001}', f(2), f(1), f(1.5)]],
  ["non-finite input", ["atr", '{"period":1}', f(2), f(1), f(NaN)]],
  ["close-only name", ["sma", '{"period":1}', f(2), f(1), f(1.5)]],
  ["overflow", ["atr", '{"period":1}', f(1.7e308), f(-1.7e308), f(0)]],
]) {
  let t = false;
  let isError = false;
  try { wasm.ohlc_indicator_series(...args); } catch (e) { t = true; isError = e instanceof Error; }
  if (!t || !isError) { failures++; console.error(`FAIL ohlc ${what} must throw an Error`); }
}
if (wasm.ohlc_indicator_series("atr", '{"period":3}', f(), f(), f()).length !== 0) {
  failures++; console.error("FAIL ohlc empty input must give empty output");
}
if (failures) process.exit(1);
console.log(`wasm conformance: ${fx.cases.length} vectors ok, ${ofx.cases.length} ohlc vectors ok`);
