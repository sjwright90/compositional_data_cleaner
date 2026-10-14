"""Project-type profiles as data (spec §4, §6.3). v1 ships the `water` profile only."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    required_fields: tuple[str, ...]
    sample_key: tuple[str, ...]
    sample_types: tuple[str, ...]


WATER = Profile(
    name="water",
    required_fields=("location_id", "sample_date"),
    sample_key=("location_id", "sample_date"),
    sample_types=(
        "normal",
        "field_dup",
        "split",
        "blank_field",
        "blank_equipment",
        "blank_trip",
        "lab_qc",
        "other",
    ),
)

PROFILES: dict[str, Profile] = {WATER.name: WATER}


def get_profile(name: str) -> Profile:
    try:
        return PROFILES[name]
    except KeyError:
        hint = " It is not available in v1 (deferred)." if name == "physical" else ""
        raise ValueError(
            f"Unknown project type {name!r}.{hint} Available: {sorted(PROFILES)}"
        ) from None
