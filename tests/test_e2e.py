"""End to end: a real uvicorn process, real HTTP, real SSE, real SQLite.

Everything except the model is genuine. A stub Ollama stands in for inference
so the run is deterministic and finishes in seconds -- the thing under test is
the pipeline, not whether a 1.5B model has an opinion worth having.

The server gets its own XDG directories, so the suite cannot reach the user's
settings or deliberations.
"""

import json
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from tests.stub_ollama import StubOllama

pytestmark = pytest.mark.e2e

QUESTION = "Should we ship the smaller thing first?"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def stub():
    with StubOllama() as server:
        yield server


@pytest.fixture(scope="module")
def server(stub, tmp_path_factory):
    """A real uvicorn process, isolated from the user's data."""
    root = tmp_path_factory.mktemp("e2e-home")
    port = _free_port()
    env = {
        **os.environ,
        "SENTINELS_OLLAMA_HOST": stub.url,
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "PYTHONUNBUFFERED": "1",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(port),
         "--log-level", "warning"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 45
        while time.time() < deadline:
            if process.poll() is not None:
                pytest.fail(f"server exited early:\n{process.stdout.read()}")
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        else:
            pytest.fail("server did not become healthy in time")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def run_council(base: str, classification: str = "sealed") -> tuple[str, list[dict]]:
    """Post a question and consume its SSE stream. Returns the id and events."""
    run_id = httpx.post(
        f"{base}/council/ask",
        json={"question": QUESTION, "classification": classification},
        timeout=10,
    ).json()["id"]

    events, event = [], {}
    with httpx.stream("GET", f"{base}/council/events/{run_id}", timeout=90) as stream:
        for line in stream.iter_lines():
            if line.startswith("event: "):
                event["event"] = line[7:]
            elif line.startswith("data: "):
                event["data"] = json.loads(line[6:])
                events.append(dict(event))
                if event["event"] == "done":
                    break
    return run_id, events


def test_the_whole_pipeline_runs_over_http(server):
    _, events = run_council(server)
    names = [e["event"] for e in events]
    assert names[0] == "opened"
    assert names[-1] == "done"
    assert [e["data"]["n"] for e in events if e["event"] == "stage"] == [1, 2, 3]
    assert "brief" in names


def test_the_app_really_talked_to_the_model_server(server, stub):
    """Proves the HTTP path was exercised, not a patched client."""
    before = len(stub.requests)
    run_council(server)
    assert len(stub.requests) > before
    assert any(r.get("format") for r in stub.requests), "chairman call was not constrained"


def test_the_brief_persists_and_exports(server):
    run_id, _ = run_council(server)
    brief = httpx.get(f"{server}/council/briefs/{run_id}", timeout=10).json()
    assert brief["brief"]["decision"]
    markdown = httpx.get(f"{server}/council/briefs/{run_id}/markdown", timeout=10).json()
    assert "## Decision" in markdown["markdown"]


def test_the_audit_chain_verifies_after_a_real_run(server):
    run_id, events = run_council(server)
    logged = httpx.get(f"{server}/council/briefs/{run_id}/events", timeout=10).json()
    assert len(logged) == len(events)
    assert httpx.get(f"{server}/council/briefs/{run_id}/verify", timeout=10).json()["ok"]


def test_a_sealed_run_reports_zero_bytes_sent(server):
    run_council(server, classification="sealed")
    status = httpx.get(f"{server}/council/status", timeout=10).json()
    assert status["egress_bytes"] == 0


def test_settings_survive_a_round_trip_over_http(server):
    body = {
        "context": "Sentinels is a council.",
        "default_classification": "sealed",
        "bench": [
            {"name": "Builder", "model": "stub-a", "persona": "Builder", "is_cloud": False},
            {"name": "Auditor", "model": "stub-b", "persona": "Auditor", "is_cloud": False},
        ],
        "chairman": {"name": "Chair", "model": "stub-a", "persona": "Chair", "is_cloud": False},
    }
    assert httpx.put(f"{server}/council/settings", json=body, timeout=10).status_code == 200
    assert httpx.get(f"{server}/council/status", timeout=10).json()["context_set"] is True


def test_the_configured_bench_is_the_one_that_runs(server):
    """Settings written over HTTP must reach the next deliberation.

    Writes its own bench rather than leaning on the previous test: a suite whose
    tests only pass in order hides the failure it is supposed to report.
    """
    httpx.put(f"{server}/council/settings", timeout=10, json={
        "context": "Sentinels is a council.",
        "default_classification": "sealed",
        "bench": [
            {"name": "Warden", "model": "stub-a", "persona": "Warden", "is_cloud": False},
            {"name": "Herald", "model": "stub-b", "persona": "Herald", "is_cloud": False},
        ],
        "chairman": {"name": "Chair", "model": "stub-a", "persona": "Chair", "is_cloud": False},
    })
    _, events = run_council(server)
    spoke = [e["data"]["member"] for e in events if e["event"] == "opinion"]
    assert spoke == ["Warden", "Herald"]


def test_the_page_and_its_assets_are_served(server):
    assert httpx.get(server, timeout=10).status_code == 200
    for asset in ("app.js", "styles.css", "fonts.css"):
        assert httpx.get(f"{server}/app/{asset}", timeout=10).status_code == 200
