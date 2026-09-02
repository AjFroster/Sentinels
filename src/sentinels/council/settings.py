"""Persisted settings.

Only one setting really matters yet: the project context. Small models have no
idea what your project is, and without that paragraph they answer a different
question with total confidence -- measured, not assumed. It was hardcoded in
the frontend; it belongs here, where it survives a restart and can be edited.

Stored as JSON under the user's config directory rather than in the repo: the
context describes your work, and on a sealed-by-default tool that is exactly
the kind of text that should not land in version control by accident.
"""

import json
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def config_path() -> Path:
    """Where settings live. Honours XDG, falls back to ~/.config."""
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "sentinels" / "settings.json"


class MemberConfig(BaseModel):
    """One seat on the council, as the user configured it."""

    name: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=120)
    persona: str = Field(
        min_length=1, max_length=300,
        description="How this member is told to behave. Written into their prompt.",
    )
    is_cloud: bool = Field(
        default=False,
        description="Cloud members are refused on sealed questions.",
    )


DEFAULT_MEMBERS = [
    MemberConfig(name="Advocate", model="qwen2.5:1.5b",
                 persona="Advocate, who argues for the proposal"),
    MemberConfig(name="Skeptic", model="llama3.2:1b",
                 persona="Skeptic, who looks for what breaks"),
    MemberConfig(name="Pragmatist", model="qwen2.5:1.5b",
                 persona="Pragmatist, who weighs effort against payoff"),
]
DEFAULT_CHAIRMAN = MemberConfig(
    name="Chairman", model="qwen2.5:1.5b",
    persona="Chairman, who synthesises without adding new claims",
)


class Settings(BaseModel):
    """Everything the council needs that is not a single question."""

    context: str = Field(
        default="",
        max_length=4000,
        description=(
            "What the council should know before answering anything. Describe the "
            "project, the constraints, and the vocabulary. This is prepended to "
            "every member's prompt."
        ),
    )
    default_classification: str = Field(
        default="sealed",
        description="Which lane a new question starts in. Sealed is the safe default.",
    )
    bench: list[MemberConfig] = Field(
        default_factory=lambda: list(DEFAULT_MEMBERS),
        min_length=2, max_length=7,
        description=(
            "The council. Published work on multi-agent debate puts the useful "
            "range at three to seven; past that you pay serial time for little gain."
        ),
    )
    chairman: MemberConfig = Field(
        default_factory=lambda: DEFAULT_CHAIRMAN.model_copy(),
        description="Who writes the brief. May be a bench member or a separate seat.",
    )

    @field_validator("bench")
    @classmethod
    def _unique_names(cls, bench: list[MemberConfig]) -> list[MemberConfig]:
        names = [m.name.strip().lower() for m in bench]
        if len(set(names)) != len(names):
            raise ValueError("Two members share a name; the transcript would be ambiguous")
        return bench

    def model_diversity(self) -> int:
        """How many distinct models sit on the bench.

        One is the weak case: personas of a single model largely rephrase each
        other, which is the failure mode a council is supposed to avoid.
        """
        return len({m.model for m in self.bench})

    @staticmethod
    def load(path: Optional[Path] = None) -> "Settings":
        """Read settings, falling back to defaults on anything unreadable.

        A corrupt settings file must not stop the app from starting -- the user
        can always retype a paragraph, but a council that refuses to boot is
        useless.
        """
        target = path or config_path()
        try:
            return Settings.model_validate_json(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Settings()

    def save(self, path: Optional[Path] = None) -> Path:
        """Write settings, creating the directory on first run."""
        target = path or config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        # Written user-only: the context describes your work, not the world's.
        target.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        try:
            target.chmod(0o600)
        except OSError:
            pass  # Windows and some mounts do not support it; not worth failing over.
        return target


STARTER_CONTEXT = (
    "Sentinels is a personal software project: a council of AI language models that "
    "deliberate on a question and produce a written brief. It runs entirely on one "
    "laptop using Ollama with small local models. Cloud members would mean calling a "
    "hosted API instead of a local model."
)
