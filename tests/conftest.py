"""Shared fixtures.

Every test runs against a temporary database and settings file. Nothing here
touches ~/.config or ~/.local/share -- a test suite that can clobber the user's
real deliberations is worse than no test suite.
"""

import pytest

from sentinels.council.runner import Council, Member
from sentinels.council.settings import Settings
from sentinels.council.store import Store

BRIEF_JSON = (
    '{"decision":"Ship the smaller thing first.",'
    '"rationale":["It is reversible.","It is cheap.","It teaches us the shape."],'
    '"dissent":["The Skeptic wanted the full build."],'
    '"constraints":["Must run offline."],'
    '"open_questions":["Whether the bench needs a fourth member."]}'
)


@pytest.fixture
def store(tmp_path) -> Store:
    """A Store pointed at a throwaway database."""
    return Store(tmp_path / "test.db")


@pytest.fixture
async def ready_store(store) -> Store:
    await store.init()
    return store


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """Redirect settings to a temp path so the real config is never touched."""
    target = tmp_path / "settings.json"
    monkeypatch.setattr("sentinels.council.settings.config_path", lambda: target)
    return target


class FakeOllama:
    """Stands in for ollama.AsyncClient.

    Records every prompt so tests can assert on what each member was actually
    asked -- the context paragraph and the anonymisation both live in prompts,
    so that is where they have to be checked.
    """

    def __init__(self, brief_json: str = BRIEF_JSON):
        self.calls: list[dict] = []
        self.brief_json = brief_json

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        # A request carrying a schema is the chairman's; everything else is a
        # member speaking in prose.
        if kwargs.get("format"):
            return {"response": self.brief_json}
        return {"response": f"opinion from {kwargs['model']}"}

    def prompts(self) -> list[str]:
        return [c["prompt"] for c in self.calls]


@pytest.fixture
def fake_ollama() -> FakeOllama:
    return FakeOllama()


@pytest.fixture
def council(fake_ollama) -> Council:
    """A three-member local council wired to the fake backend."""
    bench = [
        Member("Advocate", "model-a", "Advocate, who argues for it"),
        Member("Skeptic", "model-b", "Skeptic, who looks for what breaks"),
    ]
    chairman = Member("Chairman", "model-a", "Chairman")
    c = Council(bench, chairman)
    c.client = fake_ollama
    return c


@pytest.fixture
def cloud_council(fake_ollama) -> Council:
    """A council with one cloud member, for testing the sealed lane."""
    bench = [
        Member("Advocate", "model-a", "Advocate"),
        Member("Skeptic", "model-b", "Skeptic"),
        Member("Claude", "claude-opus-5", "Verifier", is_cloud=True),
    ]
    c = Council(bench, Member("Chairman", "model-a", "Chairman"))
    c.client = fake_ollama
    return c
