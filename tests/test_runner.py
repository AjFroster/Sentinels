"""The runner enforces the two claims the project is built on: sealed questions
never reach a cloud member, and stage 2 ranks without names attached.
"""

import pytest

from sentinels.council.brief import Classification
from sentinels.council.runner import Council, EgressRefused, Member, council_from_settings
from sentinels.council.settings import MemberConfig, Settings


async def test_sealed_question_drops_cloud_members(cloud_council: Council):
    record = await cloud_council.deliberate("q", Classification.SEALED)
    assert "Claude" not in record.members
    assert record.members == ["Advocate", "Skeptic"]


async def test_open_question_seats_cloud_members(cloud_council: Council):
    record = await cloud_council.deliberate("q", Classification.OPEN)
    assert "Claude" in record.members


async def test_sealed_question_with_a_cloud_chairman_is_refused(fake_ollama):
    """Silently demoting the chairman would be worse than failing loudly."""
    council = Council(
        [Member("Advocate", "m", "Advocate")],
        Member("Claude", "claude-opus-5", "Chairman", is_cloud=True),
    )
    council.client = fake_ollama
    with pytest.raises(EgressRefused, match="cannot leave the machine"):
        await council.deliberate("q", Classification.SEALED)


async def test_refusal_happens_before_any_model_is_called(fake_ollama):
    """The guard has to run before inference, not after."""
    council = Council(
        [Member("Advocate", "m", "Advocate")],
        Member("Claude", "c", "Chairman", is_cloud=True),
    )
    council.client = fake_ollama
    with pytest.raises(EgressRefused):
        await council.deliberate("q", Classification.SEALED)
    assert fake_ollama.calls == []


async def test_a_sealed_bench_of_only_cloud_members_is_refused(fake_ollama):
    council = Council(
        [Member("Claude", "c", "Verifier", is_cloud=True)],
        Member("Chairman", "m", "Chairman"),
    )
    council.client = fake_ollama
    with pytest.raises(EgressRefused, match="No local members"):
        await council.deliberate("q", Classification.SEALED)


async def test_exclusion_is_announced_as_an_event(cloud_council: Council):
    seen = []

    async def emit(event, data):
        seen.append((event, data))

    await cloud_council.deliberate("q", Classification.SEALED, emit=emit)
    excluded = [d for e, d in seen if e == "excluded"]
    assert excluded and excluded[0]["members"] == ["Claude"]


async def test_stages_run_in_order(council: Council):
    order = []

    async def emit(event, data):
        if event == "stage":
            order.append(data["n"])

    await council.deliberate("q", Classification.SEALED, emit=emit)
    assert order == [1, 2, 3]


async def test_every_member_is_asked_once_per_stage(council: Council, fake_ollama):
    await council.deliberate("q", Classification.SEALED)
    # two members x (opinion + critique) + one chairman call
    assert len(fake_ollama.calls) == 5


async def test_cross_examination_hides_author_names(council: Council, fake_ollama):
    """The anonymity is the mechanism that stops members deferring to whoever
    sounded most confident. If names leak into stage 2, it is theatre."""
    await council.deliberate("q", Classification.SEALED)
    ranking_prompts = [p for p in fake_ollama.prompts() if "Rank these" in p]
    assert ranking_prompts
    for prompt in ranking_prompts:
        assert "Respondent A" in prompt
        assert "Advocate" not in prompt
        assert "Skeptic" not in prompt


async def test_context_reaches_every_member(council: Council, fake_ollama):
    await council.deliberate("q", Classification.SEALED, context="Sentinels is a council.")
    opinion_prompts = [p for p in fake_ollama.prompts() if "advisory council" in p]
    assert opinion_prompts
    assert all("Sentinels is a council." in p for p in opinion_prompts)


async def test_thread_count_is_pinned_on_every_call(council: Council, fake_ollama):
    """Measured: 16 threads collapses this CPU to ~1.2 tok/s against ~22 at 6."""
    await council.deliberate("q", Classification.SEALED)
    assert all(c["options"]["num_thread"] == 6 for c in fake_ollama.calls)


async def test_chairman_call_is_schema_constrained(council: Council, fake_ollama):
    """Constrained decoding is what lets a 1.5B model emit a valid brief."""
    await council.deliberate("q", Classification.SEALED)
    constrained = [c for c in fake_ollama.calls if c.get("format")]
    assert len(constrained) == 1
    assert "decision" in constrained[0]["format"]["properties"]


async def test_sealed_record_reports_no_egress(council: Council):
    assert (await council.deliberate("q", Classification.SEALED)).egress is None


async def test_open_record_records_that_the_question_crossed(council: Council):
    assert (await council.deliberate("q", Classification.OPEN)).egress == "full question"


async def test_council_is_built_from_settings(fake_ollama):
    """The bench used to be hardcoded; it is now the user's to change."""
    settings = Settings(bench=[
        MemberConfig(name="Builder", model="model-a", persona="Builder"),
        MemberConfig(name="Auditor", model="model-b", persona="Auditor"),
    ])
    council = council_from_settings(settings)
    council.client = fake_ollama
    record = await council.deliberate("q", Classification.SEALED)
    assert record.members == ["Builder", "Auditor"]
