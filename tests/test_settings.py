"""Settings carry the bench and the context -- the two things that decide
whether output is useful. Both have to survive a round trip and a schema that
grew after the file was first written.
"""

import pytest
from pydantic import ValidationError

from sentinels.council.settings import MemberConfig, Settings


def member(name="A", model="m", persona="p", is_cloud=False) -> MemberConfig:
    return MemberConfig(name=name, model=model, persona=persona, is_cloud=is_cloud)


def test_defaults_give_a_usable_council():
    s = Settings()
    assert 2 <= len(s.bench) <= 7
    assert s.chairman.model
    assert s.default_classification == "sealed"


def test_sealed_is_the_default_lane():
    """Defaulting to open would leak by omission."""
    assert Settings().default_classification == "sealed"


def test_round_trip_through_disk(tmp_path):
    path = tmp_path / "s.json"
    original = Settings(context="ctx", bench=[member("A"), member("B", "n")])
    original.save(path)
    assert Settings.load(path).context == "ctx"
    assert [m.name for m in Settings.load(path).bench] == ["A", "B"]


def test_saved_file_is_user_only(tmp_path):
    """The context describes your work; other accounts have no business in it."""
    path = tmp_path / "s.json"
    Settings(context="private").save(path)
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_old_file_without_a_bench_still_loads(tmp_path):
    """Back-compat: bench and chairman arrived after the first release."""
    path = tmp_path / "s.json"
    path.write_text('{"context":"x","default_classification":"sealed"}')
    loaded = Settings.load(path)
    assert loaded.context == "x"
    assert len(loaded.bench) >= 2


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    """A council that refuses to boot over a bad config file is useless."""
    path = tmp_path / "s.json"
    path.write_text("{ not json")
    assert Settings.load(path).context == ""


def test_missing_file_falls_back_to_defaults(tmp_path):
    assert Settings.load(tmp_path / "absent.json").context == ""


def test_duplicate_member_names_are_rejected():
    """Two members sharing a name makes the transcript ambiguous."""
    with pytest.raises(ValidationError, match="share a name"):
        Settings(bench=[member("Advocate"), member("advocate")])


def test_a_bench_of_one_is_rejected():
    """One member is not a council."""
    with pytest.raises(ValidationError):
        Settings(bench=[member("Only")])


def test_a_bench_over_seven_is_rejected():
    """Past seven you pay serial time on this hardware for very little."""
    with pytest.raises(ValidationError):
        Settings(bench=[member(f"M{i}") for i in range(8)])


def test_model_diversity_counts_distinct_models():
    same = Settings(bench=[member("A", "m"), member("B", "m"), member("C", "m")])
    mixed = Settings(bench=[member("A", "m"), member("B", "n"), member("C", "o")])
    assert same.model_diversity() == 1     # the weak case the UI warns about
    assert mixed.model_diversity() == 3


def test_context_has_an_upper_bound():
    with pytest.raises(ValidationError):
        Settings(context="x" * 4001)
