"""Recipe, gate and migration tests (spec §8; addendum A3, A9)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel, ValidationError

from core.migrations import CURRENT, MIGRATIONS, RecipeVersionError, migrate
from core.pipeline import ReportItem
from core.recipe import (
    AcceptDefault,
    Gate,
    Recipe,
    RecipeError,
    RecipeSettings,
    SourceFile,
    accept_defaults,
    assemble_recipe,
    build_recipe,
    gate_default_drift,
    gate_field,
    gate_fields,
    load_recipe,
    recipe_hash,
    recipe_to_json,
    unresolved_gates,
)

MIGRATION_FIXTURES = Path(__file__).parent / "fixtures" / "migrations"


class Inner(BaseModel):
    mode: Gate[Literal["a", "b"]] = gate_field("a")


class DemoParams(BaseModel):
    threshold: Gate[int] = gate_field(3)
    extra: int = 7
    inner: Inner = Inner()


class PlainParams(BaseModel):
    n: int = 1


# ---- gates ----


def test_gate_fields_found_by_introspection_with_dotted_paths() -> None:
    specs = gate_fields(DemoParams)
    assert {p: s.default for p, s in specs.items()} == {"threshold": 3, "inner.mode": "a"}
    assert gate_fields(PlainParams) == {}


def test_unresolved_gates_lists_every_unset_field() -> None:
    assert unresolved_gates("demo", DemoParams()) == ["demo.inner.mode", "demo.threshold"]


def test_explicit_decision_resolves_gate() -> None:
    p = DemoParams(threshold=5, inner=Inner(mode="b"))
    assert unresolved_gates("demo", p) == []


def test_accept_default_stores_resolved_value() -> None:
    p = accept_defaults(DemoParams(threshold=5))
    assert isinstance(p.threshold, int) and p.threshold == 5
    assert isinstance(p.inner.mode, AcceptDefault)
    assert p.inner.mode.value == "a"
    assert unresolved_gates("demo", p) == []
    dumped = p.model_dump(mode="json")
    assert dumped["inner"]["mode"] == {"accept_default": True, "value": "a"}


def test_accept_defaults_does_not_mutate_input() -> None:
    p = DemoParams()
    accept_defaults(p)
    assert p.threshold is None


def test_accept_default_marker_requires_true_and_value() -> None:
    with pytest.raises(ValidationError):
        DemoParams.model_validate({"threshold": {"accept_default": False, "value": 3}})
    with pytest.raises(ValidationError):
        DemoParams.model_validate({"threshold": {"accept_default": True}})


def test_accept_default_then_default_changes_warns() -> None:
    accepted = accept_defaults(DemoParams())
    text = accepted.model_dump_json()

    class Changed(BaseModel):
        threshold: Gate[int] = gate_field(10)
        extra: int = 7
        inner: Inner = Inner()

    reloaded = Changed.model_validate_json(text)
    items = gate_default_drift("demo", reloaded)
    assert len(items) == 1
    item = items[0]
    assert item.target == "demo.threshold"
    assert item.warning is not None and "3" in item.warning and "10" in item.warning
    assert dict(item.params) == {"stored": 3, "current": 10}


def test_no_drift_warning_when_default_unchanged() -> None:
    reloaded = DemoParams.model_validate_json(accept_defaults(DemoParams()).model_dump_json())
    assert gate_default_drift("demo", reloaded) == ()


# ---- recipe ----


def _recipe(tmp_path: Path, params: DemoParams | None = None) -> Recipe:
    src = tmp_path / "raw" / "lab_a.csv"
    src.parent.mkdir(exist_ok=True)
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    return build_recipe(
        project_root=tmp_path,
        source_paths=[src],
        config_snapshot={"files": {"tokens.json": ["h1"]}, "analytes": []},
        settings=RecipeSettings(min_obs_per_cell=3),
        stages={"demo": params or DemoParams()},
    )


def test_source_paths_are_relative_posix_with_sha256(tmp_path: Path) -> None:
    r = _recipe(tmp_path)
    assert r.sources == [SourceFile(path="raw/lab_a.csv", sha256=r.sources[0].sha256)]
    assert len(r.sources[0].sha256) == 64


def test_source_outside_project_root_is_rejected(tmp_path: Path) -> None:
    other = tmp_path.parent / "elsewhere.csv"
    other.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="inside the project folder"):
        build_recipe(
            project_root=tmp_path,
            source_paths=[other],
            config_snapshot={},
            settings=RecipeSettings(min_obs_per_cell=3),
            stages={},
        )


def test_assemble_recipe_rejects_paths_outside_project_like_build_recipe() -> None:
    settings = RecipeSettings(min_obs_per_cell=3)
    for bad in ("../x.csv", "/abs/x.csv", "C:/data/x.csv", "raw/../../x.csv"):
        with pytest.raises(ValueError, match="inside the project folder"):
            assemble_recipe(
                sources=[SourceFile(path=bad, sha256="0" * 64)],
                config_snapshot={},
                settings=settings,
                params={},
            )


def test_assemble_recipe_sorts_sources_and_dumps_params() -> None:
    r = assemble_recipe(
        sources=[
            SourceFile(path="b.csv", sha256="1" * 64),
            SourceFile(path="a.csv", sha256="2" * 64),
        ],
        config_snapshot={},
        settings=RecipeSettings(min_obs_per_cell=3),
        params={"demo": DemoParams()},
    )
    assert [s.path for s in r.sources] == ["a.csv", "b.csv"]
    assert r.stages == {"demo": DemoParams().model_dump(mode="json")}


def test_load_recipe_reports_stage_field_problems_with_path() -> None:
    text = json.dumps(
        {
            "schema_version": 1,
            "settings": {"min_obs_per_cell": 3},
            "stages": {"demo": {"extra": "not a number"}},
        }
    )
    with pytest.raises(RecipeError) as exc:
        load_recipe(text, {"demo": DemoParams})
    assert any(p.startswith("stages.demo.extra:") for p in exc.value.problems)


def test_load_recipe_reports_top_level_problems() -> None:
    text = json.dumps({"schema_version": 1, "settings": {"min_obs_per_cell": "many"}})
    with pytest.raises(RecipeError) as exc:
        load_recipe(text, {"demo": DemoParams})
    assert any(p.startswith("settings.min_obs_per_cell:") for p in exc.value.problems)


def test_recipe_round_trips(tmp_path: Path) -> None:
    r = _recipe(tmp_path, accept_defaults(DemoParams(threshold=4)))
    loaded = load_recipe(recipe_to_json(r), {"demo": DemoParams})
    assert loaded.recipe == r
    assert loaded.params["demo"] == accept_defaults(DemoParams(threshold=4))
    assert loaded.report == ()


def test_recipe_records_min_obs_per_cell(tmp_path: Path) -> None:
    assert _recipe(tmp_path).settings.min_obs_per_cell == 3


def test_recipe_hash_is_deterministic_across_builds(tmp_path: Path) -> None:
    assert recipe_hash(_recipe(tmp_path)) == recipe_hash(_recipe(tmp_path))
    assert recipe_to_json(_recipe(tmp_path)) == recipe_to_json(_recipe(tmp_path))


def test_recipe_hash_changes_with_params(tmp_path: Path) -> None:
    assert recipe_hash(_recipe(tmp_path)) != recipe_hash(_recipe(tmp_path, DemoParams(threshold=9)))


def test_recipe_json_has_no_timestamps(tmp_path: Path) -> None:
    keys: set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                keys.add(k)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(recipe_to_json(_recipe(tmp_path))))
    assert not {k for k in keys if "time" in k or "date" in k or k.endswith("_at")}


def test_load_reports_unknown_stage_section(tmp_path: Path) -> None:
    text = recipe_to_json(_recipe(tmp_path))
    with pytest.raises(ValueError, match="demo"):
        load_recipe(text, {})


def test_load_emits_drift_warning(tmp_path: Path) -> None:
    text = recipe_to_json(_recipe(tmp_path, accept_defaults(DemoParams())))

    class Changed(BaseModel):
        threshold: Gate[int] = gate_field(10)
        extra: int = 7
        inner: Inner = Inner()

    loaded = load_recipe(text, {"demo": Changed})
    assert [i.target for i in loaded.report] == ["demo.threshold"]
    assert loaded.report[0].warning


# ---- migrations ----


def _v0_to_v1(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    out["sources"] = [{"path": s["file"], "sha256": s["sha256"]} for s in raw["sources"]]
    out["schema_version"] = 1
    return out


def test_production_registry_is_empty_and_current_is_one() -> None:
    assert MIGRATIONS == {}
    assert CURRENT == 1


def test_current_version_needs_no_migration() -> None:
    raw = {"schema_version": 1, "x": 1}
    out, report = migrate(raw)
    assert out == raw and report == ()


def test_old_recipe_migrates_through_injected_chain_and_logs() -> None:
    raw = json.loads((MIGRATION_FIXTURES / "recipe_v0.json").read_text(encoding="utf-8"))
    out, report = migrate(raw, registry={0: _v0_to_v1}, current=1)
    assert out["schema_version"] == 1
    assert out["sources"] == [{"path": "raw/lab_a.csv", "sha256": "abc123"}]
    assert raw["schema_version"] == 0  # input untouched
    assert len(report) == 1
    item: ReportItem = report[0]
    assert item.action == "migration"
    assert dict(item.params) == {"from_version": 0, "to_version": 1}


def test_multi_step_chain_logs_each_step() -> None:
    def bump(raw: dict[str, Any]) -> dict[str, Any]:
        return {**raw, "schema_version": raw["schema_version"] + 1}

    out, report = migrate({"schema_version": 0}, registry={0: bump, 1: bump}, current=2)
    assert out["schema_version"] == 2
    assert [dict(i.params)["from_version"] for i in report] == [0, 1]


def test_newer_recipe_raises_plain_language_error() -> None:
    with pytest.raises(RecipeVersionError, match="newer version of this app"):
        migrate({"schema_version": CURRENT + 1})


def test_missing_migration_step_raises() -> None:
    with pytest.raises(RecipeVersionError, match="version 0"):
        migrate({"schema_version": 0})


def test_missing_schema_version_raises() -> None:
    with pytest.raises(RecipeVersionError, match="schema_version"):
        migrate({})


def test_load_recipe_runs_migration_and_returns_item(tmp_path: Path) -> None:
    text = recipe_to_json(_recipe(tmp_path))
    old = json.loads(text)
    old["schema_version"] = 0

    def step(raw: dict[str, Any]) -> dict[str, Any]:
        return {**raw, "schema_version": 1}

    loaded = load_recipe(json.dumps(old), {"demo": DemoParams}, registry={0: step})
    assert [i.action for i in loaded.report] == ["migration"]
