# ADR 0022: AI strategy verifier

Date: 2026-10-11. Status: **accepted**. Roadmap story: decides D7; implements E5-S5
("AI strategy verifier: AST, import allowlist, subprocess with rlimits, no network, deterministic
smoke, look-ahead check, red-team tests; ADR D7", `docs/ROADMAP.md:296`). Depends on E4-S4
(look-ahead truncation property, `crates/honba-strategy/src/tests/look_ahead.rs`).

## Context

Strategies can be authored by an LLM (`honba-strategies/ai_generated/`, ADR 0009 goal 4) and the
Python loader (`python/src/honba/strategies/loader.py`) executes a strategy file in-process with
`importlib`. Today nothing stands between LLM output and that import:

- `honba/ai/verification/` exists but is empty (`critic.py`, `nl_to_strategy.py`,
  `regime_report.py` are all 0 bytes); there is no `__init__.py`.
- `verify` in the tree means **manifest** verification (`honba.strategies.verify.verify_manifest`
  → Rust `StrategyIr`), not code verification. REST explicitly rejects a `code`/`source` body
  (ADR 0013, `422 source_unsupported`).
- The only AST + allow/deny machinery in the repo is the adapter boundary checker
  (`python/src/honba/adapters/boundary.py`: `_imported_modules`, `find_boundary_violations`). Its
  shape — parse imports, compare top-level module names, return typed violations — is the model to
  reuse, but its forbidden set is broker SDKs, not process/network/filesystem escapes.
- ADR 0009 already states the intent, at a design level and still **Proposed**: "parser / AST
  allow-list sandbox … no network, no FS outside strategy dir, no dunder escapes", "generated code
  is never auto-started live" (`docs/adr/0009-agentic-mcp-and-local-llm-integration.md:294,299`).
  It does not choose a mechanism; D7 leaves open "subprocess with rlimits vs container".
- The deterministic in-process harness already exists: `honba.strategies.testing.replay`
  (`python/src/honba/strategies/testing.py:57`) runs a `Strategy` over `Bar`s and returns its
  intents and fills. `honba.ai.trust` (E5-S2) gives the envelope for wrapping untrusted text.

The threat model is an LLM (or a strategy author) that produces Python which is imported and run
by the research loop. The verifier must decide, before the loader ever imports the file, whether it
is safe to run and whether it is causal, and it must run it without letting it touch the network,
spawn processes, exhaust the machine or outlive a bounded budget.

## Decision

### 1. Placement and shape

Code verification is Python-only infrastructure and lives in the existing package
`honba/ai/verification/` (populated now; `nl_to_strategy.py`, `critic.py`, `regime_report.py` stay
untouched empty placeholders for separate stories). Public surface:

```
verify_source(source, *, filename="<strategy>", config=None, bars=None,
              limits=None, timeout_s=..., check_look_ahead=True) -> VerificationReport
verify_strategy_file(path, **kwargs) -> VerificationReport
scan_source(source, *, filename="<strategy>") -> list[Finding]
run_in_sandbox(source, *, filename, config, bars, limits, timeout_s,
               check_look_ahead) -> SandboxOutcome
check_look_ahead(build, bars) -> LookAheadReport
```

`verify_source` runs the static scan first and **short-circuits**: a static violation is returned
with `sandbox=None`, so unsafe code is never executed even in the limited subprocess. Only a
statically clean source reaches the sandbox.

### 2. Static scan: AST import allowlist and escape denylist

Pure and dependency-free (`ast` only), mirroring `adapters/boundary.py`. It returns `Finding`s
with a stable `code`, a message and a line/column:

- **`forbidden_import`** — any `ast.Import` / `ast.ImportFrom` (level 0) whose top-level module is
  not on the allowlist. Allowlist: a fixed set of pure stdlib modules
  (`math`, `statistics`, `collections`, `dataclasses`, `typing`, `typing_extensions`,
  `itertools`, `functools`, `operator`, `enum`, `abc`, `decimal`, `fractions`, `numbers`, `bisect`,
  `heapq`, `array`, `copy`, `json`, `re`, `string`, `random`, `__future__`), the numeric stack
  (`numpy`, `pandas`), and a **vetted list of `honba` submodules** (`honba.domain`, `honba.entities`,
  `honba.strategies.base`, `honba.strategies.config`, `honba.strategies.indicators`,
  `honba.strategies.manifest`, `honba.strategies.sizing`, `honba.backtest`). The loader,
  verification, `ai`, adapters and `data` packages are deliberately excluded because they expose
  `os`/`sys`/`importlib`/HTTP clients as attributes. `time` and `datetime` are deliberately absent:
  a verifiable strategy is wall-clock free.
- **`forbidden_name`** — a call to `eval`, `exec`, `compile`, `__import__`, `open`, `input`,
  `breakpoint`, `globals`, `locals`, `vars`, `exit`, `quit`, `getattr`, `setattr`, `delattr`,
  or `type`. The match is restricted to bare `ast.Name` so `df.eval(...)` is not a false positive.
- **`forbidden_dunder`** — an attribute access to `__globals__`, `__subclasses__`, `__bases__`,
  `__mro__`, `__builtins__`, `__import__`, `__loader__`, `__code__`, `__closure__`, `f_globals`,
  `f_locals`, `__class__`, `__dict__`, `__reduce__`, `__reduce_ex__`, `__getattribute__`,
  `__self__`, `__func__`, `__wrapped__`, `gi_frame`, `cr_frame`, `tb_frame`. This catches the
  `().__class__.__mro__` chain and similar climbs.
- **`syntax_error`** — `ast.parse` raises; the finding carries the message and location.

The allowlist is the security boundary; the name/dunder denylist catches the common escapes that
need no import. Static analysis decides safety, not correctness: a source can be empty of findings
and still be wrong, which is what the sandbox run is for.

### 3. Sandbox: a subprocess with resource limits (D7)

D7 asks "subprocess with rlimits vs container". **Decision: subprocess with `resource` limits.** A
container is the right answer only for a hosted multi-tenant verifier (deferred, and a non-goal
here); in every other setting it adds a runtime dependency the research loop does not have. The
sandbox is:

- A fresh interpreter launched as `[sys.executable, "-B", "-m", "honba.ai.verification._bootstrap"]`
  with a controlled environment: only `PATH`, `PYTHONPATH` pinned to the running `honba` package's
  parent, `PYTHONHASHSEED=0`, `LANG` and `TMPDIR` (the sandbox's own temporary directory) are set;
  nothing else is inherited, so no `PYTHONSTARTUP`/`PYTHONWARNINGS`/`PYTHONUSERBASE` leaks in. `HOME`
  is deliberately left unset so the interpreter still finds the user-site packages (`httpx`,
  `numpy`, …) the core imports. The job (source, config, serialized bars, limits) arrives as JSON on
  stdin; the **verdict is written to a parent-provided result file path** (`result.ndjson` in the
  sandbox temp dir) which the child opens with `os.open(O_WRONLY|O_CREAT|O_TRUNC)` and writes a
  single sentinel-prefixed JSON line. Child stdout/stderr are redirected to capped log files under
  `RLIMIT_FSIZE` and are never trusted; they are captured for diagnostics only. This prevents a
  strategy from forging the verdict by writing to its own stdout.
- Resource limits are set inside the child before the strategy is imported, via
  `resource.setrlimit`: `RLIMIT_CPU` (CPU seconds), `RLIMIT_AS` (address space → memory),
  `RLIMIT_FSIZE` (bytes written), `RLIMIT_NOFILE` (open files), `RLIMIT_CORE=0`. `RLIMIT_AS` is
  computed as the child's own baseline `VmSize` (read after `honba` is imported) plus
  `address_space_mb`, so the limit accommodates the numeric stack instead of fighting it. Process
  creation is **not** limited with `RLIMIT_NPROC`: it is per-uid, so a low value is unreliable and
  can break thread creation; `fork`/`exec` are refused by the audit hook instead (§ runtime guards).
  `RLIMIT_CPU` kills a runaway loop; `RLIMIT_AS` turns an allocation bomb into a catchable
  `MemoryError`; over-limit kills surface to the parent as a signal return code.
- The parent also enforces a wall-clock `timeout_s` on the child (belt and braces for a strategy
  blocked in C without consuming CPU).

**No network / no process / no filesystem escape** is enforced at runtime by a `sys.addaudithook`
installed in the child before the strategy is imported: it raises on `socket.*`, `subprocess.*`,
`ctypes.*`, `marshal.*`, `pty.*`, `os.system`, `os.exec*`, `os.spawn*`, `os.fork`, `os.forkpty`,
`os.posix_spawn`, `os.kill`, `os.chdir`, `os.chflags`, `os.chmod`, `os.chown`, `os.link`,
`os.mkdir`, `os.mknod`, `os.remove`, `os.removexattr`, `os.rename`, `os.rmdir`, `os.setxattr`,
`os.symlink`, `os.system`, `os.truncate`, `os.unsetenv`, `os.utime`, `mmap.__new__`,
`pickle.find_class`, and on `open`/`os.open`/`os.remove`/`os.rename`/`os.mkdir`/`os.chmod`/
`os.link`/`os.symlink`/`os.truncate`/`os.utime` (writes). Imports stay allowed (the hook fires on
the dangerous *events*, not on reading site-packages), so `numpy`/`pandas` and `honba` still load.
This is defense in depth: the static allowlist already refuses `import socket` and `import os`, and
the audit hook catches the same operations if the allowlist is ever weakened.

### 4. Deterministic smoke

The child imports the strategy, finds exactly one `Strategy` subclass, builds it (see §6), and runs
`replay` over a fixed bar series (the caller's `bars`, or a deterministic 32-bar synthetic series).
It records intents and fills and a digest. It then **builds and runs the strategy a second time in
the same process and requires an identical digest**; a mismatch is `nondeterministic` (the classic
case is an unseeded `random`). `random` is on the allowlist so this check has teeth; wall-clock
imports are refused statically so time-based nondeterminism is a static error, not a flaky one.

### 5. Look-ahead check

The verifier applies the E4-S4 property to the untrusted strategy: for every prefix of the bar
series, the prefix run's intents must be exactly the full run's intents truncated at the prefix
(the full run's submission-ordered intent list must start with the prefix run's list). A
future-peeking strategy emits different early decisions on a short prefix and is reported as
`look_ahead` with the offending prefix length. `check_look_ahead(build, bars)` is the reusable
primitive (mirrors Rust `assert_truncation_property`); `build(bars)` receives the run's own bars so
a vectorized strategy that precomputes over its input can be caught.

### 6. The vectorized build hook

By default the child instantiates the class the way the loader does: `cls(config)` if the
constructor takes a positional, else `cls()`. If the strategy module defines a top-level callable
`build_strategy(bars)`, the verifier uses it instead, passing the current run's bars. This is the
verifier's contract for vectorized/AI strategies that precompute indicators over their input
(mirroring Rust's `build(&bars[..prefix])`), and it is what makes the look-ahead check able to
catch the vectorized shape rather than being vacuous. It is not a change to the core `Strategy`
interface and no core code calls it.

### 7. Report and stable codes

`VerificationReport` carries the source, the static findings, and (unless short-circuited) the
`SandboxOutcome`; `.ok` is true only when both the static scan and the sandbox run are clean.
Findings use string codes: `forbidden_import`, `forbidden_name`, `forbidden_dunder`, `syntax_error`,
`no_strategy`, `multiple_strategies`, `construction_error`, `smoke_failed`, `nondeterministic`,
`look_ahead`, `sandbox_timeout`, `sandbox_limit`, `sandbox_denied`, `sandbox_crash`. Every failure
mode that can cross the surface carries one of these codes.

### 8. Red-team corpus

The story ships tests that attack the verifier: forbidden imports (`os`, `socket`, `subprocess`),
dunder escapes (`().__class__.__mro__`), `eval`/`__import__`/`open`, an infinite loop (CPU limit),
a memory bomb (address-space limit), a runtime network attempt and a runtime process-spawn attempt
run through `run_in_sandbox` directly (bypassing the static scan, to prove the runtime guard), an
unseeded `random` strategy (nondeterminism) and a future-peeking strategy (look-ahead). Each test
asserts the expected `code` and that the verifier returns within a bounded time.

## Consequences

- LLM-authored strategies get a mechanical pre-import gate: safe to run, causal, deterministic,
  bounded. AI-generated strategies pass the same suite as hand-written ones (`verify_source`),
  satisfying E5-S5's acceptance tests and ADR 0009's "validated and sandboxed" intent without
  accepting ADR 0009's whole MCP design.
- The sandbox is a security boundary only against non-adversarial LLM output. A determined attacker
  who can already write the Python source runs in the same OS user; the defenses (allowlist,
  audit hook, rlimits, no network) raise the cost and catch accidents and common escapes, not a
  kernel exploit. This is stated as a known limit, not hidden.
- The verifier runs the strategy in a child process, so it can only import a strategy file by
  source string or path, not a live class; `verify_source` is the unit and `verify_strategy_file`
  reads the file. Nothing in the core runtime depends on the verifier.

## Out of scope

| Item | Disposition |
|---|---|
| REST `POST /strategies/verify-source` route | Deferred; ADR 0013 keeps code bodies out of REST. Would need codegen + a route decision. |
| MCP tool `verify_strategy_source` | Deferred; tool schemas are generated from Rust (`mcp_tools.json`). Separate story. |
| `honba verify` CLI subcommand for source | Deferred; CLI verification of code is a thin wrapper to add when a caller wants it. |
| Container/namespace isolation, seccomp | Deferred to a hosted verifier; D7 chose subprocess. |
| LLM critic / NL-to-strategy generation | Separate stories; the empty `ai/verification/` placeholders stay for them. |
| Wiring into `honba-strategies/ai_generated` promotion gate | The gate that consumes the verifier; own story once a generated strategy exists. |

## Known limits

- Read access to arbitrary paths is not blocked (only writes outside the sandbox temp dir and the
  dangerous audit events are); blocking reads would block importing `honba`/`numpy`. Reviewing
  suspicious `open(...)` reads is the static scan's job and it refuses `open` outright.
- `RLIMIT_AS` is enforced on virtual address space and can be defeated by a strategy that leaks
  memory across the two smoke runs; the CPU and wall-clock limits still bound a run.
- The determinism check compares one digest over one data series. Two runs agreeing is strong
  evidence, not proof, of determinism.
- `run_on_tick`/quote/depth hooks other than `on_bar` are exercised only if the strategy uses them
  on the bar series; the verifier's harness is bar-driven like `replay`.

## References

- `docs/ROADMAP.md:296` (E5-S5), `:346` (D7), `:184` (AI code verification).
- `docs/adr/0009-agentic-mcp-and-local-llm-integration.md` (Proposed) §"Safety invariants".
- `docs/adr/0013-rest-read-ports.md` (`source_unsupported`).
- `python/src/honba/adapters/boundary.py` (AST + allowlist precedent).
- `crates/honba-strategy/src/tests/look_ahead.rs` (truncation property).
- `python/src/honba/strategies/testing.py` (`replay`), `python/src/honba/ai/trust.py` (E5-S2).
