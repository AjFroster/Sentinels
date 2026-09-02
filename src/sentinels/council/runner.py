"""Three-stage council: opinions -> blind cross-examination -> chairman brief.

Stages are structured on purpose. Published work on multi-agent debate finds
unguided discussion among similar models can underperform a single model
correcting itself; the anonymised ranking in stage 2 is what stops members
deferring to whoever sounded most confident.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from ollama import AsyncClient

from .brief import Brief, BriefRecord, Classification, build_chairman_prompt
from .config import ollama_host

# Measured on an i7-1260P: 6 threads peaks at ~22 tok/s, 16 threads collapses
# to ~1.2 tok/s because the E-cores stall the per-layer sync barrier.
NUM_THREAD = 6


class EgressRefused(RuntimeError):
    """A cloud member was asked to answer a sealed question."""


@dataclass(frozen=True)
class Member:
    """One seat on the council."""

    name: str
    model: str
    persona: str
    is_cloud: bool = False


# An async callback the runner uses to report progress. The web layer turns
# these into server-sent events; a CLI can just print them.
EventSink = Callable[[str, dict], Awaitable[None]]


async def _noop(event: str, data: dict) -> None:
    return None


class Council:
    def __init__(self, members: list[Member], chairman: Member,
                 host: str | None = None):
        self.members = members
        self.chairman = chairman
        self.client = AsyncClient(host=host or ollama_host())

    def _seat(self, classification: Classification) -> list[Member]:
        """Drop cloud members from a sealed question, and say so."""
        if classification is not Classification.SEALED:
            return self.members
        return [m for m in self.members if not m.is_cloud]

    async def _ask(self, member: Member, prompt: str, num_predict: int,
                   schema: dict | None = None) -> str:
        request = {
            "model": member.model,
            "prompt": prompt,
            "options": {"num_thread": NUM_THREAD, "num_predict": num_predict,
                        "temperature": 0.8},
            "keep_alive": "30m",
        }
        if schema is not None:
            request["format"] = schema
        response = await self.client.generate(**request)
        return response["response"].strip()

    async def deliberate(self, question: str,
                         classification: Classification = Classification.SEALED,
                         context: str = "",
                         emit: Optional[EventSink] = None) -> BriefRecord:
        emit = emit or _noop
        started = time.monotonic()
        seated = self._seat(classification)
        excluded = [m for m in self.members if m not in seated]
        if excluded:
            await emit("excluded", {
                "members": [m.name for m in excluded],
                "reason": f"{classification.value} question cannot reach a cloud member",
            })
        if not seated:
            raise EgressRefused(
                f"No local members available for a {classification.value} question"
            )
        if classification is Classification.SEALED and self.chairman.is_cloud:
            raise EgressRefused(
                f"Chairman {self.chairman.name!r} is a cloud member; "
                "a sealed question cannot leave the machine"
            )

        # Stage 1 -- independent opinions, no member sees another's answer.
        await emit("stage", {"n": 1, "name": "Independent opinions",
                             "members": [m.name for m in seated]})

        async def opinion(m: Member) -> str:
            await emit("thinking", {"member": m.name, "model": m.model, "stage": 1})
            text = await self._ask(
                m, f"You are the {m.persona} on an advisory council.\n"
                   f"{('Context: ' + context) if context else ''}\n"
                   f"Question: {question}\n\n"
                   f"Give your opinion in under 120 words.", 160)
            await emit("opinion", {"member": m.name, "model": m.model, "text": text})
            return text

        opinions = await asyncio.gather(*(opinion(m) for m in seated))
        named = list(zip((m.name for m in seated), opinions))

        # Stage 2 -- authorship stripped so ranking cannot follow reputation.
        anon = "\n\n".join(
            f"Respondent {chr(65 + i)}: {text[:600]}"
            for i, (_, text) in enumerate(named)
        )
        await emit("stage", {"n": 2, "name": "Blind cross-examination",
                             "members": [m.name for m in seated]})

        async def critique(m: Member, label: str) -> str:
            await emit("thinking", {"member": label, "model": "masked", "stage": 2})
            text = await self._ask(
                m, f"Question: {question}\n\n{anon}\n\n"
                   f"Rank these best to worst and say what is wrong with "
                   f"each. Under 80 words.", 120)
            await emit("critique", {"member": label, "text": text})
            return text

        critiques = await asyncio.gather(*(
            critique(m, f"Respondent {chr(65 + i)}") for i, m in enumerate(seated)
        ))

        # Stage 3 -- chairman fills the brief under a constrained schema.
        await emit("stage", {"n": 3, "name": "Chairman verdict",
                             "members": [self.chairman.name]})
        await emit("thinking", {"member": self.chairman.name,
                                "model": self.chairman.model, "stage": 3})
        raw = await self._ask(
            self.chairman,
            build_chairman_prompt(question, named, list(critiques), context),
            600,
            schema=Brief.model_json_schema(),
        )

        record = BriefRecord(
            brief=Brief.model_validate_json(raw),
            question=question,
            classification=classification,
            members=[m.name for m in seated],
            chairman=self.chairman.name,
            stages_elapsed_s=time.monotonic() - started,
            egress=None if classification is Classification.SEALED else "full question",
        )
        await emit("brief", record.model_dump(mode="json"))
        return record


def council_from_settings(settings) -> "Council":
    """Build a council from saved settings.

    The bench used to be hardcoded here. It is the measured quality lever --
    two members on the same model mostly rephrase each other -- so it belongs
    somewhere the user can change it without editing Python.
    """
    members = [
        Member(m.name, m.model, m.persona, m.is_cloud) for m in settings.bench
    ]
    chairman = Member(
        settings.chairman.name, settings.chairman.model,
        settings.chairman.persona, settings.chairman.is_cloud,
    )
    return Council(members, chairman)
