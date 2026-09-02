"""The Brief — the handoff artifact a council deliberation produces.

Sentinels deliberates; it does not implement. A finished council run emits a
Brief: a structured decision record an implementer (a person, or a tool-equipped
agent like Claude Code) can act on without re-reading the whole transcript.

The schema is deliberately flat. Ollama constrains decoding to a JSON schema,
which is what lets a 1.5B model emit valid structure -- but nested objects and
long enums degrade badly at that size, so every field here is a string or a
list of strings.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Classification(str, Enum):
    """Who was allowed to see the question."""

    SEALED = "sealed"      # local bench only, never left the machine
    OPEN = "open"          # full council including cloud members
    REDACTED = "redacted"  # identifiers stripped locally, then escalated


class Brief(BaseModel):
    """What the chairman produces at the end of stage 3."""

    decision: str = Field(
        description="What the council landed on. One or two sentences, imperative."
    )
    rationale: list[str] = Field(
        min_length=2, max_length=4,
        description="Why. Three bullets, each a complete standalone reason.",
    )
    dissent: list[str] = Field(
        description=(
            "Minority positions, quoted rather than paraphrased. "
            "Empty list only if the council was genuinely unanimous."
        )
    )
    constraints: list[str] = Field(
        min_length=1,
        description="What any implementation must honour. Testable statements.",
    )
    open_questions: list[str] = Field(
        min_length=1,
        description=(
            "What the council could not resolve, and what evidence would settle it. "
            "Never empty -- a council that resolved everything did not look hard enough."
        )
    )


class BriefRecord(BaseModel):
    """A Brief plus the provenance needed to trust it later."""

    brief: Brief
    question: str
    classification: Classification
    members: list[str]
    chairman: str
    stages_elapsed_s: float
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    egress: Optional[str] = Field(
        default=None,
        description="What crossed the machine boundary, if anything. None means nothing did.",
    )

    def to_markdown(self) -> str:
        """Render for pasting into an implementer.

        Ordered decision-first so the reader can stop after two lines if that is
        all they needed.
        """
        b = self.brief
        seal = {
            Classification.SEALED: "SEALED - deliberated locally, nothing left this machine",
            Classification.OPEN: "OPEN - full council, cloud members included",
            Classification.REDACTED: f"REDACTED - escalated after stripping: {self.egress or 'unspecified'}",
        }[self.classification]

        def bullets(items: list[str], empty: str) -> str:
            return "\n".join(f"- {i}" for i in items) if items else f"- _{empty}_"

        return "\n".join([
            f"# Brief: {self.question}",
            "",
            f"> {seal}",
            f"> Council: {', '.join(self.members)} | Chairman: {self.chairman}",
            f"> Deliberated {self.stages_elapsed_s:.0f}s on {self.created_at:%Y-%m-%d %H:%M UTC}",
            "",
            "## Decision",
            b.decision,
            "",
            "## Rationale",
            bullets(b.rationale, "none recorded"),
            "",
            "## Dissent",
            bullets(b.dissent, "council was unanimous"),
            "",
            "## Constraints",
            bullets(b.constraints, "none recorded"),
            "",
            "## Open questions",
            bullets(b.open_questions, "none recorded - treat this as a warning, not a clean bill"),
            "",
            "---",
            "_Produced by a Sentinels council. Opinions are model output, not verified fact --",
            "an implementer with tools should check any claim before acting on it._",
        ])


CHAIRMAN_PROMPT = """You are the chairman of an advisory council. The council has \
finished deliberating and you must now record the outcome as a brief.

CONTEXT
{context}

QUESTION
{question}

COUNCIL OPINIONS
{opinions}

CROSS-EXAMINATION
{critiques}

Write the brief for someone who was not in the room. Rules:
- Never write "Respondent A", "Respondent B" or any letter label. Those are
  scaffolding from the anonymous ranking round. Name the member, or describe the
  position without attributing it at all.
- decision: what the council landed on, imperative, one or two sentences.
- rationale: exactly three bullets. Each must stand alone without the others.
- dissent: quote minority positions in their own words. Do not smooth them into \
agreement. Leave empty ONLY if no member disagreed.
- constraints: what an implementer must honour. Each must be checkable, and
  each must constrain an implementation -- not describe how the council felt.
- open_questions: what the council could not settle, and what evidence would \
settle it. Never leave this empty.

Report only what the council actually said. Do not add findings of your own, and \
do not state as fact anything no member verified."""


def build_chairman_prompt(question: str, opinions: list[tuple[str, str]],
                          critiques: list[str], context: str = "") -> str:
    """Assemble the stage-3 prompt from the deliberation so far.

    ``context`` matters more than it looks. Small models have no idea what your
    project is; without it they will confidently answer a different question.
    """
    return CHAIRMAN_PROMPT.format(
        context=context or "(none given)",
        question=question,
        opinions="\n\n".join(f"{name}: {text}" for name, text in opinions),
        critiques="\n\n".join(critiques) if critiques else "(none recorded)",
    )
