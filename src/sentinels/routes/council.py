"""HTTP surface for the council.

Deliberations are slow -- 80 to 200 seconds on CPU -- so the interesting
endpoint is the event stream, not the result. A request starts a background
run and returns immediately; the browser watches stages land over SSE.
"""

import asyncio
import json
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from sentinels.council import BriefRecord, Classification
from sentinels.council.config import ollama_host
from sentinels.council.settings import STARTER_CONTEXT, Settings, config_path
from sentinels.council.store import Store, db_path
from sentinels.council.runner import Council, EgressRefused, council_from_settings

router = APIRouter(prefix="/council", tags=["council"])

# Live runs only. Anything finished lives in the store, so a restart loses
# nothing except deliberations that were still mid-flight.
_queues: dict[str, asyncio.Queue] = {}

store = Store()


class AskRequest(BaseModel):
    question: str = Field(min_length=8)
    classification: Classification = Classification.SEALED
    context: Optional[str] = Field(
        default=None,
        description="Override the saved context for this question only. "
                    "Normally left unset -- the server uses the stored setting.",
    )


class AskResponse(BaseModel):
    id: str


@router.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    """Start a deliberation. Returns immediately -- watch /events/{id}."""
    # The stored context is the source of truth; a request may override it for a
    # one-off, but the client is never responsible for remembering it.
    context = req.context if req.context is not None else Settings.load().context

    run_id = uuid.uuid4().hex[:12]
    queue: asyncio.Queue = asyncio.Queue()
    _queues[run_id] = queue

    # Context is prepended to every member's prompt, so on an open or redacted
    # question it crosses the boundary too. Count it.
    egress_bytes = (
        0 if req.classification is Classification.SEALED
        else len(req.question.encode()) + len(context.encode())
    )

    async def emit(event: str, data: dict) -> None:
        # Persist before streaming: the log is the record of what happened, and
        # a browser that disconnects must not be able to lose an entry.
        await store.append(run_id, event, data)
        await queue.put({"event": event, "data": data})

    await emit("opened", {
        "question": req.question,
        "classification": req.classification.value,
        "context_chars": len(context),
        "egress_bytes": egress_bytes,
    })

    async def run() -> None:
        try:
            council = council_from_settings(Settings.load())
            record = await council.deliberate(
                req.question, req.classification, context, emit=emit
            )
            payload = record.model_dump(mode="json")
            payload["egress_bytes"] = egress_bytes
            await store.save(run_id, payload)
        except EgressRefused as exc:
            await emit("error", {"message": str(exc), "kind": "egress"})
        except Exception as exc:  # noqa: BLE001 - surface anything to the UI
            await emit("error", {"message": f"{type(exc).__name__}: {exc}",
                                 "kind": "runtime"})
        finally:
            await emit("done", {"id": run_id})

    asyncio.create_task(run())
    return AskResponse(id=run_id)


@router.get("/events/{run_id}")
async def events(run_id: str) -> StreamingResponse:
    """Server-sent events for one deliberation."""
    queue = _queues.get(run_id)
    if queue is None:
        raise HTTPException(404, f"No deliberation {run_id!r}")

    async def stream():
        # Nudge the browser's EventSource to open before the first slow stage.
        yield ": open\n\n"
        while True:
            item = await queue.get()
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"
            if item["event"] == "done":
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/briefs")
async def list_briefs() -> list[dict]:
    """Newest first, for the sidebar."""
    return await store.recent()


async def _load(run_id: str) -> BriefRecord:
    row = await store.get(run_id)
    if row is None:
        raise HTTPException(404, f"No brief for {run_id!r}")
    return BriefRecord.model_validate(row)


@router.get("/briefs/{run_id}")
async def get_brief(run_id: str) -> BriefRecord:
    return await _load(run_id)


@router.get("/briefs/{run_id}/markdown")
async def get_markdown(run_id: str) -> dict:
    """The handoff artifact, ready to paste into an implementer."""
    record = await _load(run_id)
    return {"markdown": record.to_markdown()}


@router.get("/briefs/{run_id}/events")
async def get_events(run_id: str) -> list[dict]:
    """The audit trail: every stage transition, in order, hash-chained."""
    rows = await store.events(run_id)
    if not rows:
        raise HTTPException(404, f"No events for {run_id!r}")
    return rows


@router.get("/briefs/{run_id}/verify")
async def verify(run_id: str) -> dict:
    """Recompute the hash chain. Proves the log has not been edited since."""
    return await store.verify(run_id)


@router.get("/settings", response_model=Settings)
async def get_settings() -> Settings:
    """Current settings. Returns defaults if nothing has been saved yet."""
    return Settings.load()


@router.put("/settings", response_model=Settings)
async def put_settings(settings: Settings) -> Settings:
    """Persist settings to the user config directory."""
    try:
        settings.save()
    except OSError as exc:
        raise HTTPException(500, f"Could not write settings: {exc}") from exc
    return settings


@router.get("/models")
async def models() -> dict:
    """Models Ollama can actually serve, for the bench picker."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{ollama_host()}/api/tags")
            response.raise_for_status()
            installed = sorted(
                {m["name"] for m in response.json().get("models", [])}
            )
    except Exception:  # noqa: BLE001 - an unreachable Ollama is a UI state
        return {"reachable": False, "installed": []}
    return {"reachable": True, "installed": installed}


@router.get("/settings/meta")
async def settings_meta() -> dict:
    """Where settings live, and a starter context for an empty install."""
    saved = Settings.load()
    return {
        "path": str(config_path()),
        "exists": config_path().exists(),
        "context_set": bool(saved.context.strip()),
        "starter_context": STARTER_CONTEXT,
    }


def _memory() -> dict:
    """Read WSL's view of memory. The gauge in the status bar wants this."""
    values = {}
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                values[key] = int(rest.strip().split()[0])
    except OSError:
        return {"used_mb": 0, "total_mb": 0}
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    return {"used_mb": (total - available) // 1024, "total_mb": total // 1024}


@router.get("/status")
async def status() -> dict:
    """Bench state for the status bar: what is loaded, memory, bytes sent."""
    loaded: list[dict] = []
    reachable = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{ollama_host()}/api/ps")
            response.raise_for_status()
            reachable = True
            loaded = [
                {"model": m.get("name", "?"), "size_mb": m.get("size", 0) // (1024 * 1024)}
                for m in response.json().get("models", [])
            ]
    except Exception:  # noqa: BLE001 - Ollama being down is a UI state, not a 500
        pass

    settings = Settings.load()
    return {
        "context_set": bool(settings.context.strip()),
        "db_path": str(db_path()),
        "ollama_reachable": reachable,
        "loaded": loaded,
        "memory": _memory(),
        "egress_bytes": await store.egress_total(),
        "bench": [
            {"name": m.name, "model": m.model, "is_cloud": m.is_cloud}
            for m in settings.bench
        ],
        "chairman": settings.chairman.name,
        # One distinct model means the bench will mostly agree with itself.
        "model_diversity": settings.model_diversity(),
    }
