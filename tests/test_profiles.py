"""Project-type profile tests (spec §4 profiles-are-data, §6.3, §2 glossary)."""

from __future__ import annotations

import dataclasses

import pytest

from core.profiles import PROFILES, Profile, get_profile


def test_water_profile_requires_location_and_date() -> None:
    p = get_profile("water")
    assert p.name == "water"
    assert set(p.required_fields) == {"location_id", "sample_date"}
    assert p.sample_key == ("location_id", "sample_date")


def test_canonical_sample_types_match_glossary() -> None:
    assert set(get_profile("water").sample_types) == {
        "normal",
        "field_dup",
        "split",
        "blank_field",
        "blank_equipment",
        "blank_trip",
        "lab_qc",
        "other",
    }


def test_physical_profile_not_enabled() -> None:
    with pytest.raises(ValueError, match="physical"):
        get_profile("physical")
    assert "physical" not in PROFILES


def test_unknown_profile_raises() -> None:
    with pytest.raises(ValueError, match="nope"):
        get_profile("nope")


def test_profile_is_frozen_data() -> None:
    p = get_profile("water")
    assert isinstance(p, Profile)
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.name = "x"  # type: ignore[misc]
