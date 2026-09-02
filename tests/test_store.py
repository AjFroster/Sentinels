"""The audit log is the project's trust claim, so it gets the most tests.

If the chain can be silently corrupted, "this question never left the machine"
is an assertion rather than a fact.
"""

import asyncio
import sqlite3

import pytest

from sentinels.council.store import GENESIS, Store


async def test_chain_starts_at_genesis(ready_store: Store):
    await ready_store.append("run", "opened", {"question": "why"})
    events = await ready_store.events("run")
    assert events[0]["prev_hash"] == GENESIS
    assert events[0]["seq"] == 0


async def test_each_event_links_to_the_previous(ready_store: Store):
    for i in range(4):
        await ready_store.append("run", "stage", {"n": i})
    events = await ready_store.events("run")
    for earlier, later in zip(events, events[1:]):
        assert later["prev_hash"] == earlier["hash"]


async def test_verify_passes_on_an_untouched_chain(ready_store: Store):
    for i in range(5):
        await ready_store.append("run", "stage", {"n": i})
    result = await ready_store.verify("run")
    assert result["ok"] is True
    assert result["checked"] == 5


async def test_verify_detects_an_edited_event(ready_store: Store):
    """The case the chain exists for: someone rewrites history in the db."""
    for i in range(3):
        await ready_store.append("run", "opinion", {"member": f"M{i}"})
    with sqlite3.connect(ready_store.path) as conn:
        conn.execute(
            "UPDATE events SET data=? WHERE deliberation_id=? AND seq=1",
            ('{"member":"Someone Else"}', "run"),
        )
    result = await ready_store.verify("run")
    assert result["ok"] is False
    assert "altered" in result["reason"]


async def test_verify_detects_a_deleted_event(ready_store: Store):
    for i in range(4):
        await ready_store.append("run", "stage", {"n": i})
    with sqlite3.connect(ready_store.path) as conn:
        conn.execute("DELETE FROM events WHERE deliberation_id=? AND seq=1", ("run",))
    result = await ready_store.verify("run")
    assert result["ok"] is False


async def test_verify_reports_an_empty_log_rather_than_passing(ready_store: Store):
    """An absent log must never read as a verified one."""
    result = await ready_store.verify("never-ran")
    assert result["ok"] is False
    assert result["checked"] == 0


async def test_concurrent_appends_do_not_collide(ready_store: Store):
    """Regression: stage 1 runs every member at once.

    Appending read the current max seq and then inserted, so two members racing
    produced a duplicate primary key and killed the deliberation.
    """
    await asyncio.gather(*(
        ready_store.append("run", "thinking", {"i": i}) for i in range(16)
    ))
    events = await ready_store.events("run")
    assert [e["seq"] for e in events] == list(range(16))
    assert (await ready_store.verify("run"))["ok"] is True


async def test_chains_are_independent_per_deliberation(ready_store: Store):
    await ready_store.append("a", "opened", {})
    await ready_store.append("b", "opened", {})
    assert (await ready_store.events("b"))[0]["prev_hash"] == GENESIS


async def test_brief_survives_a_new_store_instance(ready_store: Store, tmp_path):
    """Persistence: a fresh process must read what the old one wrote."""
    record = {
        "question": "q", "classification": "sealed",
        "created_at": "2026-09-02T00:00:00+00:00", "stages_elapsed_s": 12.0,
        "chairman": "Chairman", "members": ["A", "B"],
        "brief": {"decision": "d"}, "egress": None, "egress_bytes": 0,
    }
    await ready_store.save("run", record)

    reopened = Store(ready_store.path)
    assert (await reopened.get("run"))["question"] == "q"
    assert len(await reopened.recent()) == 1


async def test_egress_total_sums_across_deliberations(ready_store: Store):
    base = {
        "question": "q", "classification": "open",
        "created_at": "2026-09-02T00:00:00+00:00", "stages_elapsed_s": 1.0,
        "chairman": "C", "members": [], "brief": {}, "egress": "full question",
    }
    await ready_store.save("a", {**base, "egress_bytes": 120})
    await ready_store.save("b", {**base, "egress_bytes": 80})
    await ready_store.save("c", {**base, "classification": "sealed", "egress_bytes": 0})
    assert await ready_store.egress_total() == 200


async def test_egress_total_is_zero_when_everything_was_sealed(ready_store: Store):
    """The titlebar's central claim, asserted."""
    await ready_store.save("a", {
        "question": "q", "classification": "sealed",
        "created_at": "2026-09-02T00:00:00+00:00", "stages_elapsed_s": 1.0,
        "chairman": "C", "members": [], "brief": {}, "egress": None, "egress_bytes": 0,
    })
    assert await ready_store.egress_total() == 0


async def test_missing_brief_reads_as_none(ready_store: Store):
    assert await ready_store.get("nope") is None
