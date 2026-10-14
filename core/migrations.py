"""Recipe schema migrations, chained by version (spec §8)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from core.pipeline import ReportItem

logger = logging.getLogger(__name__)

CURRENT = 1

Migration = Callable[[dict[str, Any]], dict[str, Any]]

# Key = version the step migrates FROM; each step returns a dict at version + 1.
MIGRATIONS: Mapping[int, Migration] = {}


class RecipeVersionError(ValueError):
    """The recipe cannot be brought to the schema version this app understands."""


def migrate(
    raw: Mapping[str, Any],
    *,
    registry: Mapping[int, Migration] | None = None,
    current: int | None = None,
) -> tuple[dict[str, Any], tuple[ReportItem, ...]]:
    """Run the migration chain on a raw recipe dict; never mutates `raw`."""
    steps = MIGRATIONS if registry is None else registry
    target = CURRENT if current is None else current
    version = raw.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise RecipeVersionError("This recipe has no valid 'schema_version', so it cannot be read.")
    if version > target:
        raise RecipeVersionError(
            f"This recipe was written by a newer version of this app (recipe version {version}, "
            f"this app reads up to {target}). Update the app to open it."
        )
    out = dict(raw)
    report: list[ReportItem] = []
    while version < target:
        step = steps.get(version)
        if step is None:
            raise RecipeVersionError(
                f"This recipe is at version {version} and no upgrade path to version "
                f"{version + 1} is available."
            )
        out = step(dict(out))
        new_version = out.get("schema_version")
        if new_version != version + 1:
            raise RecipeVersionError(
                f"The upgrade from recipe version {version} did not produce version {version + 1}."
            )
        report.append(
            ReportItem(
                "migration",
                "recipe",
                1,
                {"from_version": version, "to_version": version + 1},
            )
        )
        logger.info("recipe migrated v%d -> v%d", version, version + 1)
        version += 1
    return out, tuple(report)
