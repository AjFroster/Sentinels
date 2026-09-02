"""Smoke: does the assembled app stand up at all?

Deliberately shallow. These are the checks that catch a broken deployment --
a missing static file, an unmountable router, a schema that will not build --
before anything slower bothers to run.
"""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.smoke


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    import sentinels.routes.council as routes
    from sentinels.council.store import Store

    monkeypatch.setattr(routes, "store", Store(tmp_path / "smoke.db"))
    monkeypatch.setattr(
        "sentinels.council.settings.config_path", lambda: tmp_path / "settings.json"
    )
    import main
    with TestClient(main.app) as client:
        yield client


def test_app_imports_and_starts(app_client):
    assert app_client.get("/health").json()["status"]


def test_openapi_schema_builds(app_client):
    """A malformed response model only shows up when the schema is generated."""
    schema = app_client.get("/openapi.json").json()
    assert "/council/ask" in schema["paths"]
    assert "/council/briefs/{run_id}/verify" in schema["paths"]


def test_every_static_asset_the_page_needs_is_served(app_client):
    html = app_client.get("/")
    assert html.status_code == 200
    for asset in ("app.js", "styles.css", "fonts.css"):
        assert app_client.get(f"/app/{asset}").status_code == 200, asset


def test_vendored_font_files_exist(app_client):
    """The page references these by name; a missing one is a silent fallback."""
    import re
    css = app_client.get("/app/fonts.css").text
    referenced = set(re.findall(r"url\((/app/fonts/[^)]+)\)", css))
    assert referenced, "fonts.css declares no faces"
    for path in referenced:
        assert app_client.get(path).status_code == 200, path


def test_status_answers_even_with_ollama_down(app_client, monkeypatch):
    """Ollama being unreachable is a UI state, not a 500."""
    monkeypatch.setenv("SENTINELS_OLLAMA_HOST", "http://127.0.0.1:1")
    body = app_client.get("/council/status").json()
    assert body["ollama_reachable"] is False
    assert body["loaded"] == []


def test_models_endpoint_answers_even_with_ollama_down(app_client, monkeypatch):
    monkeypatch.setenv("SENTINELS_OLLAMA_HOST", "http://127.0.0.1:1")
    body = app_client.get("/council/models").json()
    assert body["reachable"] is False
