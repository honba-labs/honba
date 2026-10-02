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
    return root


def _run(root: Path, *args: str, env_extra: dict | None = None):
    env = {k: v for k, v in os.environ.items() if k != "HONBA_FRONTEND_DIR"}
    env["PATH"] = str(root)  # no npx reachable: any attempt to run it would fail
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
    assert result.returncode == 0, result.stdout + result.stderr
    produced = root / "schema" / "domain" / "domain_schema.json"
    assert produced.read_text() == COMMITTED.read_text()
    assert not (tmp_path / "solo" / "honba-frontend").exists()


def test_typescript_step_skipped_when_no_frontend_dir(tmp_path):
    result = _run(_checkout(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipping typescript" in result.stdout.lower()
    assert "npx" not in result.stderr


def test_frontend_dir_argument_enables_typescript_step(tmp_path):
    root = _checkout(tmp_path)
    out = tmp_path / "ts_out"
    result = _run(root, "--frontend-dir", str(out))
    # npx is unreachable here, so the TS step is attempted and fails: proves it was not skipped.
    assert result.returncode != 0
    assert "skipping typescript" not in result.stdout.lower()


def test_frontend_dir_env_var_enables_typescript_step(tmp_path):
    root = _checkout(tmp_path)
    result = _run(root, env_extra={"HONBA_FRONTEND_DIR": str(tmp_path / "ts_env")})
    assert result.returncode != 0
    assert "skipping typescript" not in result.stdout.lower()
