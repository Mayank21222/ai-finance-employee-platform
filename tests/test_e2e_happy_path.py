"""End-to-end: real mock app in a subprocess, stub model, full graph run."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PORT = 8011
URL = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module")
def app_url():
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_app.app:app", "--port", str(PORT),
         "--log-level", "warning"],
        cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            httpx.get(URL, timeout=1.0)
            break
        except httpx.HTTPError:
            time.sleep(0.25)
    else:
        proc.terminate()
        pytest.fail(f"mock app failed to start on port {PORT}")
    yield URL
    proc.terminate()
    proc.wait(timeout=10)


@pytest.mark.parametrize(
    "failures",
    ["", "renamed_field", "popup,validation"],
    ids=["happy_path", "renamed_field", "popup_and_validation"],
)
def test_operator_completes_task(app_url, failures):
    tag = (failures or "happy").replace(",", "_").replace(" ", "")
    run_id = f"test_e2e_{tag}"
    env = {
        **os.environ,
        "MODEL_PROVIDER": "stub",
        "APP_URL": app_url,
        "TOOL_TIMEOUT_SECONDS": "15",
    }
    cmd = [
        sys.executable, "-m", "ai_operator.run",
        "--fresh", "--answer", "y", "--no-start-app",
        "--app-url", app_url, "--run-id", run_id,
    ]
    if failures:
        cmd += ["--failures", failures]
    result = subprocess.run(
        cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=240,
    )
    assert result.returncode == 0, (
        f"exit {result.returncode}\nSTDOUT:\n{result.stdout[-4000:]}"
        f"\nSTDERR:\n{result.stderr[-2000:]}"
    )
    report = json.loads((REPO_ROOT / "runs" / run_id / "report.json").read_text())
    assert report["status"] == "verified_complete"
    assert report["evidence"], "expected an evidence screenshot"
    assert not report["errors"] or "step budget" not in " ".join(report["errors"])
