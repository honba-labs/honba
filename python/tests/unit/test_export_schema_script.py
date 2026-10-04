"""scripts/export_schema.py resolves the honba checkout from its own location.

It must work in a single-repo checkout (no sibling honba-frontend, no npx): the JSON bundle is
always exported; the TypeScript step runs only when a frontend dir is given.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "export_schema.py"
COMMITTED = REPO / "schema" / "domain" / "domain_schema.json"


def _checkout(tmp_path: Path) -> Path:
    """A checkout-like layout: <root>/honba/{scripts,python}; nothing else beside it."""
    root = tmp_path / "solo" / "honba"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "export_schema.py")
    (root / "python").symlink_to(REPO / "python", target_is_directory=True)
    (root / "schema" / "domain").mkdir(parents=True)
    # Pre-build the honba binary, and make it reachable as `honba` in the checkout's PATH.
    # The new Rust-based wrapper finds `cargo`/the binary via PATH, not via python.
    cargo = shutil.which("cargo") or shutil.which("rustc") and "cargo"
    if cargo is None:
        raise RuntimeError("cargo not found; required to build honba binary for checkout test")
    # Ensure binary exists so the wrapper does not need to invoke cargo inside the bare PATH.
    subprocess.run(
        ["cargo", "build", "--bin", "honba"],
        cwd=REPO,
        check=False,
        capture_output=True,
    )
    bin_src = REPO / "target" / "debug" / ("honba.exe" if sys.platform == "win32" else "honba")
    if bin_src.is_file():
        shutil.copy(bin_src, root / ("honba.exe" if sys.platform == "win32" else "honba"))
    return root


def _run(root: Path, *args: str, env_extra: dict | None = None):
    env = {k: v for k, v in os.environ.items() if k != "HONBA_FRONTEND_DIR"}
    env["PATH"] = str(root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "export_schema.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
        check=False,
    )


def test_exports_json_without_sibling_repos_or_npx(tmp_path):
    root = _checkout(tmp_path)
    result = _run(root)
    assert result.returncode == 0, result.stderr + "\n" + result.stdout
    out = result.stderr + "\n" + result.stdout
    assert "domain_schema.json" in out or "codegen complete" in out.lower(), out
    assert not (tmp_path / "solo" / "honba-frontend").exists()


def test_typescript_step_skipped_when_no_frontend_dir(tmp_path):
    result = _run(_checkout(tmp_path))
    assert result.returncode == 0, result.stderr + result.stdout
    assert "typescript" in result.stderr.lower() or "codegen complete" in result.stderr.lower()


def test_frontend_dir_argument_enables_typescript_step(tmp_path):
    root = _checkout(tmp_path)
    out = tmp_path / "ts_out"
    result = _run(root, "--frontend-dir", str(out))
    # With a frontend dir the wrapper invokes `honba schema export --typescript <out>`;
    # the binary exists so the run succeeds and writes domain.ts.
    assert result.returncode == 0, result.stderr + result.stdout
    assert (out / "domain.ts").is_file()


def test_frontend_dir_env_var_enables_typescript_step(tmp_path):
    root = _checkout(tmp_path)
    result = _run(root, env_extra={"HONBA_FRONTEND_DIR": str(tmp_path / "ts_env")})
    assert result.returncode == 0, result.stderr + result.stdout
