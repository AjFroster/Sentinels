"""The HTTP surface the desktop shell talks to.

The store and settings are both redirected at temp paths here; a test run must
never be able to write to the real database.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from sentinels.council.runner import Council, Member
from sentinels.council.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch, fake_ollama):
    import sentinels.routes.council as routes

    monkeypatch.setattr(routes, "store", Store(tmp_path / "api.db"))
    monkeypatch.setattr(
        "sentinels.council.settings.config_path", lambda: tmp_path / "settings.json"
    )

    def local_council(_settings):
        council = Council(
            [Member("Advocate", "model-a", "Advocate"),
             Member("Skeptic", "model-b", "Skeptic")],
            Member("Chairman", "model-a", "Chairman"),
        )
        council.client = fake_ollama
        return council

    monkeypatch.setattr(routes, "council_from_settings", local_council)

    import main
    with TestClient(main.app) as c:      # lifespan creates the schema
        yield c


def deliberate(client, **body) -> str:
    """Post a question and drain its event stream. Returns the run id."""
    payload = {"question": "Should we ship the smaller thing?", **body}
    run_id = client.post("/council/ask", json=payload).json()["id"]
    with client.stream("GET", f"/council/events/{run_id}") as stream:
        for _ in stream.iter_lines():
            pass
    return run_id


def test_health(client):
    assert client.get("/health").status_code == 200


def test_index_and_assets_are_served(client):
    assert client.get("/").status_code == 200
    for asset in ("app.js", "styles.css", "fonts.css"):
        assert client.get(f"/app/{asset}").status_code == 200, asset


def test_page_does_not_reference_remote_fonts(client):
    """The titlebar claims zero bytes sent; the page must not fetch a stylesheet."""
    html = client.get("/").text
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html


def test_settings_round_trip(client):
    body = {
        "context": "Sentinels is a council.",
        "default_classification": "sealed",
        "bench": [
            {"name": "Builder", "model": "m", "persona": "Builder", "is_cloud": False},
            {"name": "Auditor", "model": "n", "persona": "Auditor", "is_cloud": False},
        ],
        "chairman": {"name": "Chair", "model": "m", "persona": "Chair", "is_cloud": False},
    }
    assert client.put("/council/settings", json=body).status_code == 200
    saved = client.get("/council/settings").json()
    assert saved["context"] == "Sentinels is a council."
    assert [m["name"] for m in saved["bench"]] == ["Builder", "Auditor"]


def test_settings_rejects_a_duplicate_named_bench(client):
    body = {
        "context": "", "default_classification": "sealed",
        "bench": [
            {"name": "A", "model": "m", "persona": "p"},
            {"name": "a", "model": "m", "persona": "p"},
        ],
        "chairman": {"name": "C", "model": "m", "persona": "p"},
    }
    assert client.put("/council/settings", json=body).status_code == 422


def test_status_reports_context_and_diversity(client):
    body = client.get("/council/status").json()
    assert body["context_set"] is False        # nothing saved yet
    assert body["model_diversity"] >= 1
    assert "db_path" in body


def test_status_notices_a_saved_context(client):
    client.put("/council/settings", json={
        "context": "Sentinels is a council.", "default_classification": "sealed",
    })
    assert client.get("/council/status").json()["context_set"] is True


def test_short_questions_are_rejected(client):
    assert client.post("/council/ask", json={"question": "why"}).status_code == 422


def test_events_for_an_unknown_run_is_404(client):
    assert client.get("/council/events/nope").status_code == 404


def test_brief_for_an_unknown_run_is_404(client):
    assert client.get("/council/briefs/nope").status_code == 404
    assert client.get("/council/briefs/nope/events").status_code == 404


def test_a_deliberation_persists_and_reads_back(client):
    run_id = deliberate(client)
    listed = client.get("/council/briefs").json()
    assert [r["id"] for r in listed] == [run_id]

    brief = client.get(f"/council/briefs/{run_id}").json()
    assert brief["brief"]["decision"]
    assert brief["classification"] == "sealed"


def test_markdown_export_is_available_after_the_run(client):
    run_id = deliberate(client)
    md = client.get(f"/council/briefs/{run_id}/markdown").json()["markdown"]
    assert "## Decision" in md
    assert "nothing left this machine" in md


def test_the_event_log_verifies_after_a_real_run(client):
    run_id = deliberate(client)
    events = client.get(f"/council/briefs/{run_id}/events").json()
    assert [e["event"] for e in events][:2] == ["opened", "stage"]
    assert events[-1]["event"] == "done"
    assert client.get(f"/council/briefs/{run_id}/verify").json()["ok"] is True


def test_sealed_run_sends_nothing(client):
    deliberate(client, classification="sealed")
    assert client.get("/council/status").json()["egress_bytes"] == 0


def test_open_run_counts_what_crossed_the_boundary(client):
    deliberate(client, classification="open")
    assert client.get("/council/status").json()["egress_bytes"] > 0


def test_egress_total_survives_a_restart(client, tmp_path):
    """The counter is derived from the database, not held in memory."""
    deliberate(client, classification="open")
    before = client.get("/council/status").json()["egress_bytes"]

    import main
    with TestClient(main.app) as fresh:
        assert fresh.get("/council/status").json()["egress_bytes"] == before
