"""The brief is the handoff artifact. Its shape is a promise to whatever reads
it next, so the required parts have to stay required.
"""

import pytest
from pydantic import ValidationError

from sentinels.council.brief import (
    Brief,
    BriefRecord,
    Classification,
    build_chairman_prompt,
)


def make_brief(**overrides) -> Brief:
    base = dict(
        decision="Ship the smaller thing.",
        rationale=["Reversible.", "Cheap.", "Teaches the shape."],
        dissent=["The Skeptic wanted the full build."],
        constraints=["Must run offline."],
        open_questions=["Whether a fourth member helps."],
    )
    return Brief(**{**base, **overrides})


def record(**overrides) -> BriefRecord:
    base = dict(
        brief=make_brief(), question="Ship small or large?",
        classification=Classification.SEALED, members=["Advocate", "Skeptic"],
        chairman="Chairman", stages_elapsed_s=93.0,
    )
    return BriefRecord(**{**base, **overrides})


def test_open_questions_cannot_be_empty():
    """A council that resolved everything did not look hard enough."""
    with pytest.raises(ValidationError):
        make_brief(open_questions=[])


def test_constraints_cannot_be_empty():
    with pytest.raises(ValidationError):
        make_brief(constraints=[])


def test_unanimity_is_allowed_to_have_no_dissent():
    assert make_brief(dissent=[]).dissent == []


def test_markdown_leads_with_the_decision():
    """Readers must be able to stop after two lines."""
    md = record().to_markdown()
    assert md.index("## Decision") < md.index("## Rationale")
    assert md.index("## Rationale") < md.index("## Dissent")


def test_sealed_markdown_states_nothing_left_the_machine():
    assert "nothing left this machine" in record().to_markdown()


def test_redacted_markdown_names_what_crossed_the_boundary():
    md = record(classification=Classification.REDACTED,
                egress="names and client identifiers").to_markdown()
    assert "REDACTED" in md
    assert "names and client identifiers" in md


def test_markdown_carries_the_not_verified_disclaimer():
    """Structure makes weak output look authoritative; say so on the page."""
    assert "not verified fact" in record().to_markdown()


def test_unanimous_brief_says_so_rather_than_showing_a_blank():
    assert "unanimous" in record(brief=make_brief(dissent=[])).to_markdown()


def test_chairman_prompt_forbids_respondent_labels():
    """Regression: the chairman used to echo stage-2 scaffolding into briefs."""
    prompt = build_chairman_prompt("q", [("Advocate", "yes")], ["ranking"])
    assert "Respondent A" in prompt          # named in the prohibition
    assert "Never write" in prompt


def test_chairman_prompt_includes_context_when_given():
    """Context is the measured quality lever; it must reach the chairman."""
    prompt = build_chairman_prompt("q", [("A", "x")], [], context="Sentinels is a council.")
    assert "Sentinels is a council." in prompt


def test_chairman_prompt_is_explicit_when_no_context_exists():
    assert "(none given)" in build_chairman_prompt("q", [("A", "x")], [])
