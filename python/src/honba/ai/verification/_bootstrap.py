"""Child entry point for the strategy sandbox (E5-S5).

Run as ``python -B -m honba.ai.verification._bootstrap`` with the job JSON on stdin. It sets its
own resource limits, installs the runtime audit hook, executes the untrusted strategy, runs the
deterministic smoke twice and the look-ahead check, and prints one sentinel-prefixed JSON result.
Never import this from trusted code; the parent drives it through ``sandbox.run_in_sandbox``.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import resource
import sys
import types
from typing import Any

from honba.ai.verification.findings import Finding, FindingCode
from honba.ai.verification.lookahead import check_look_ahead, intent_key
from honba.ai.verification.sandbox import RESULT_PREFIX
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.strategies.base import Strategy
from honba.strategies.config import StrategyConfig
from honba.strategies.testing import replay

__all__ = ["AuditDenied", "main"]


class AuditDenied(Exception):
    """A runtime audit event the sandbox refuses (network, process spawn, mutation)."""


_DENIED_PREFIXES = ("socket.", "subprocess.", "ctypes.", "marshal.", "pty.")
_DENIED_EVENTS = frozenset(
    {
        "mmap.__new__",
        "os.chdir",
        "os.chflags",
        "os.chmod",
        "os.chown",
        "os.exec",
        "os.fork",
        "os.forkpty",
        "os.kill",
        "os.link",
        "os.mkdir",
        "os.mknod",
        "os.posix_spawn",
        "os.putenv",
        "os.remove",
        "os.removexattr",
        "os.rename",
        "os.rmdir",
        "os.setxattr",
        "os.spawn",
        "os.symlink",
        "os.system",
        "os.truncate",
        "os.unsetenv",
        "os.utime",
        "pickle.find_class",
    }
)
_WRITE_OPEN_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _try_set(what: int, value: tuple[int, int]) -> None:
    try:
        resource.setrlimit(what, value)
    except (ValueError, OSError):
        pass


def _vms_bytes() -> int | None:
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmSize:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _set_address_space(extra_mb: int) -> None:
    baseline = _vms_bytes()
    if baseline is None:
        return
    limit = baseline + extra_mb * 1024 * 1024
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY:
        limit = min(limit, hard)
    _try_set(resource.RLIMIT_AS, (limit, limit))


def _set_limits(limits: dict[str, Any]) -> None:
    cpu = max(1, int(limits.get("cpu_seconds", 10)))
    _try_set(resource.RLIMIT_CPU, (cpu, cpu + 1))
    fsize = max(1, int(limits.get("file_size_mb", 8))) * 1024 * 1024
    _try_set(resource.RLIMIT_FSIZE, (fsize, fsize))
    nfiles = max(16, int(limits.get("open_files", 256)))
    _try_set(resource.RLIMIT_NOFILE, (nfiles, nfiles))
    _try_set(resource.RLIMIT_CORE, (0, 0))
    _set_address_space(max(16, int(limits.get("address_space_mb", 2048))))


def _install_audit_hook() -> None:
    def hook(event: str, args: tuple[Any, ...]) -> None:
        if event.startswith(_DENIED_PREFIXES) or event in _DENIED_EVENTS:
            raise AuditDenied(event)
        if event == "open" and args:
            mode = args[1]
            if isinstance(mode, str):
                if any(flag in mode for flag in "wax+"):
                    raise AuditDenied(f"open for write ({mode!r})")
            elif isinstance(mode, int) and mode & _WRITE_OPEN_FLAGS:
                raise AuditDenied("open for write (fd)")
        elif event == "os.open" and len(args) >= 3 and isinstance(args[2], int):
            if args[2] & _WRITE_OPEN_FLAGS:
                raise AuditDenied("os.open for write")

    sys.addaudithook(hook)


def _exec_source(source: str, filename: str) -> types.ModuleType:
    code = compile(source, filename, "exec")
    module = types.ModuleType("honba_untrusted_strategy")
    sys.modules[module.__name__] = module
    exec(code, module.__dict__)  # noqa: S102 - the sandbox's whole purpose is to run this
    return module


def _strategy_classes(module: types.ModuleType) -> list[type[Strategy]]:
    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, Strategy) and obj is not Strategy and obj.__module__ == module.__name__
    ]


def _parse_bars(raw: list[dict[str, Any]]) -> list[Bar]:
    return [
        Bar(
            InstrumentId(str(item["symbol"]), str(item["exchange"])),
            int(item["ts"]),
            float(item["open"]),
            float(item["high"]),
            float(item["low"]),
            float(item["close"]),
            float(item["volume"]),
        )
        for item in raw
    ]


def _smoke(build: Any, bars: list[Bar]) -> dict[str, Any]:
    result = replay(build(bars), bars)
    decisions = tuple(intent_key(intent) for intent in result.intents)
    digest = hashlib.sha256(repr(decisions).encode("utf-8")).hexdigest()
    return {"intents": len(result.intents), "fills": len(result.fills), "digest": digest}


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _instantiate(cls: type[Strategy], config: StrategyConfig | None) -> Strategy:
    """Build ``cls`` the way the loader does: ``cls(config)`` if it takes one positional."""
    params = [
        p
        for p in inspect.signature(cls.__init__).parameters.values()
        if p.name != "self" and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if params and config is not None:
        return cls(config)  # type: ignore[call-arg]
    return cls()


def _failed(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "smoke": None,
        "look_ahead": None,
        "findings": [Finding(code, message).to_dict()],
    }


def _run(job: dict[str, Any]) -> dict[str, Any]:
    try:
        module = _exec_source(job["source"], str(job.get("filename", "<strategy>")))
    except AuditDenied as exc:
        return _failed(FindingCode.SANDBOX_DENIED, f"blocked {exc}")
    except MemoryError:
        return _failed(FindingCode.SANDBOX_LIMIT, "out of memory while executing the strategy")
    except BaseException as exc:  # noqa: BLE001 - untrusted code; report, never propagate
        return _failed(FindingCode.CONSTRUCTION_ERROR, _describe(exc))

    classes = _strategy_classes(module)
    if not classes:
        return _failed(FindingCode.NO_STRATEGY, "the file defines no Strategy subclass")
    if len(classes) > 1:
        names = ", ".join(sorted(cls.__name__ for cls in classes))
        return _failed(
            FindingCode.MULTIPLE_STRATEGIES,
            f"expected one Strategy subclass, found {len(classes)}: {names}",
        )

    config = StrategyConfig(**job.get("config", {}))
    bars = _parse_bars(job.get("bars", []))
    builder = getattr(module, "build_strategy", None)
    if callable(builder):
        build = builder  # type: ignore[assignment]
    else:
        cls = classes[0]

        def build(
            _bars: Any, _cls: type[Strategy] = cls, _config: StrategyConfig = config
        ) -> Strategy:
            return _instantiate(_cls, _config)

    findings: list[dict[str, Any]] = []
    try:
        first = _smoke(build, bars)
        second = _smoke(build, bars)
    except AuditDenied as exc:
        return _failed(FindingCode.SANDBOX_DENIED, f"blocked {exc}")
    except MemoryError:
        return _failed(FindingCode.SANDBOX_LIMIT, "out of memory while running the strategy")
    except BaseException as exc:  # noqa: BLE001 - untrusted code; report, never propagate
        return _failed(FindingCode.SMOKE_FAILED, _describe(exc))

    if first["digest"] != second["digest"]:
        findings.append(
            Finding(
                FindingCode.NONDETERMINISTIC,
                "two runs over identical bars produced different decisions",
            ).to_dict()
        )

    look_ahead: dict[str, Any] | None = None
    if job.get("check_look_ahead", True):
        try:
            report = check_look_ahead(build, bars)
        except BaseException as exc:  # noqa: BLE001 - untrusted code; report, never propagate
            return _failed(FindingCode.SMOKE_FAILED, _describe(exc))
        look_ahead = {
            "checked_prefixes": report.checked_prefixes,
            "violations": [violation.message() for violation in report.violations],
        }
        if not report.ok:
            findings.append(
                Finding(FindingCode.LOOK_AHEAD, report.violations[0].message()).to_dict()
            )

    return {"ok": not findings, "smoke": first, "look_ahead": look_ahead, "findings": findings}


def main() -> int:
    job = json.loads(sys.stdin.read())
    result_fd = None
    result_path = job.get("result_path")
    if result_path:
        try:
            result_fd = os.open(result_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        except OSError:
            result_fd = None
    _set_limits(job.get("limits", {}))
    _install_audit_hook()
    result = _run(job)
    payload = ("\n" + RESULT_PREFIX + json.dumps(result) + "\n").encode("utf-8")
    if result_fd is not None:
        os.write(result_fd, payload)
        os.close(result_fd)
    else:
        sys.stdout.write(payload.decode("utf-8"))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
