from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from core.audit import LogRow
from core.pipeline import Status
from core.project import DependencyNotCommittedError, Project, UnresolvedGatesError
from core.recipe import AcceptDefault
from tests import stub_stages
from tests.stub_stages import (
    STUBS,
    STUBS_WITH_GAMMA,
    Alpha,
    AlphaParams,
    BetaOut,
    BetaParams,
)

FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"


@pytest.fixture(autouse=True)
def _clear_calls() -> None:
    stub_stages.CALLS.clear()


@pytest.fixture
def defaults(tmp_path: Path) -> Path:
    d = tmp_path / "defaults"
    shutil.copytree(FIXTURE_CONFIG, d)
    return d


@pytest.fixture
def project(tmp_path: Path, defaults: Path) -> Project:
    root = tmp_path / "proj"
    root.mkdir()
    return Project(root, stages=STUBS, defaults_dir=defaults)


def _accept_beta(p: Project) -> None:
    p.set_params("beta", BetaParams(mode="keep"))


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _commit_chain(p: Project) -> None:
    p.commit("alpha")
    p.set_params("beta", BetaParams(mode="keep"))
    p.commit("beta")


def _by_stage(rows: list[LogRow], stage: str) -> list[LogRow]:
    return [r for r in rows if r.stage == stage]


# ---- run_stage, cache, preview ----


def test_run_stage_returns_report_and_caches(project: Project) -> None:
    first = project.run_stage("alpha")
    again = project.run_stage("alpha")
    assert len(first.report) == 2
    assert stub_stages.CALLS == ["alpha"]
    assert again.output.equals(first.output)
    assert [(i.action, i.count) for i in again.report] == [
        (i.action, i.count) for i in first.report
    ]


def test_cache_survives_new_project_instance(project: Project, defaults: Path) -> None:
    project.run_stage("alpha")
    stub_stages.CALLS.clear()
    other = Project(project.root, stages=STUBS, defaults_dir=defaults)
    other.run_stage("alpha")
    assert stub_stages.CALLS == []


def test_dataclass_output_round_trips_through_cache(project: Project, defaults: Path) -> None:
    _accept_beta(project)
    first = project.run_stage("beta")
    other = Project(project.root, stages=STUBS, defaults_dir=defaults)
    other.set_params("beta", BetaParams(mode="keep"))
    stub_stages.CALLS.clear()
    again = other.run_stage("beta")
    assert stub_stages.CALLS == []
    assert isinstance(again.output, BetaOut)
    assert again.output.label == "beta"
    assert again.output.table.equals(first.output.table)


def test_cache_write_is_atomic_and_leaves_no_temp_dirs(project: Project) -> None:
    project.run_stage("alpha")
    entries = [p.name for p in (project.root / "cache" / "alpha").iterdir()]
    assert len(entries) == 1
    assert not any(n.startswith(".tmp") or n.endswith(".tmp") for n in entries)


def test_corrupt_cache_entry_is_a_miss_with_warning(project: Project, defaults: Path) -> None:
    project.run_stage("alpha")
    entry = next((project.root / "cache" / "alpha").iterdir())
    victim = next(p for p in entry.iterdir() if p.suffix == ".parquet")
    victim.write_bytes(b"not parquet")
    stub_stages.CALLS.clear()
    fresh = Project(project.root, stages=STUBS, defaults_dir=defaults)  # new session: no memo
    res = fresh.run_stage("alpha")
    assert stub_stages.CALLS == ["alpha"]
    warnings = [i.warning for i in res.report if i.action == "cache_miss_unreadable"]
    assert len(warnings) == 1 and warnings[0] is not None
    assert f"cache/alpha/{entry.name}" in warnings[0]


def test_incomplete_cache_entry_is_a_miss(project: Project, defaults: Path) -> None:
    project.run_stage("alpha")
    entry = next((project.root / "cache" / "alpha").iterdir())
    (entry / "meta.json").unlink()
    stub_stages.CALLS.clear()
    fresh = Project(project.root, stages=STUBS, defaults_dir=defaults)  # new session: no memo
    res = fresh.run_stage("alpha")
    assert stub_stages.CALLS == ["alpha"]
    assert any(i.action == "cache_miss_unreadable" for i in res.report)


def test_preview_writes_nothing_and_logs_nothing(project: Project) -> None:
    before = sorted(p.relative_to(project.root) for p in project.root.rglob("*"))
    res = project.preview("alpha", AlphaParams(factor=7))
    assert res.output["x"].iloc[0] == 7
    after = sorted(p.relative_to(project.root) for p in project.root.rglob("*"))
    assert before == after
    assert project.log_rows() == []
    assert project.heads == {}


def test_run_stage_unresolved_gate_raises_with_field_names(project: Project) -> None:
    with pytest.raises(UnresolvedGatesError) as exc:
        project.run_stage("beta")
    assert exc.value.fields == ["beta.mode"]
    assert "beta.mode" in str(exc.value)


# ---- staleness and heads ----


def test_run_stage_without_commit_is_not_done(project: Project) -> None:
    project.run_stage("alpha")
    assert project.staleness() == {"alpha": Status.STALE, "beta": Status.BLOCKED}
    assert project.heads == {}
    assert not (project.root / "cache" / "heads.json").exists()


def test_commit_makes_stage_done_and_writes_heads(project: Project) -> None:
    before = project.staleness()
    assert before == {"alpha": Status.STALE, "beta": Status.BLOCKED}
    project.commit("alpha")
    st = project.staleness()
    assert st["alpha"] is Status.DONE
    assert st["beta"] is Status.STALE
    heads = json.loads((project.root / "cache" / "heads.json").read_text(encoding="utf-8"))
    assert heads == {"alpha": project.fingerprint("alpha")}


def test_param_change_makes_stage_and_dependents_stale(project: Project) -> None:
    _accept_beta(project)
    project.commit("alpha")
    project.commit("beta")
    assert set(project.staleness().values()) == {Status.DONE}
    project.set_params("alpha", AlphaParams(factor=5))
    st = project.staleness()
    assert st["alpha"] is Status.STALE
    assert st["beta"] is Status.STALE


def test_config_file_change_makes_only_readers_stale(tmp_path: Path, defaults: Path) -> None:
    root = tmp_path / "proj3"
    root.mkdir()
    p = Project(root, stages=STUBS_WITH_GAMMA, defaults_dir=defaults)
    p.set_params("beta", BetaParams(mode="keep"))
    for name in ("alpha", "beta", "gamma"):
        p.commit(name)
    assert set(p.staleness().values()) == {Status.DONE}

    tokens = defaults / "tokens.json"  # read by alpha only; beta depends on alpha
    original = tokens.read_text(encoding="utf-8")
    tokens.write_text(original + "\n", encoding="utf-8")
    assert p.staleness() == {"alpha": Status.STALE, "beta": Status.STALE, "gamma": Status.DONE}
    tokens.write_text(original, encoding="utf-8")
    assert set(p.staleness().values()) == {Status.DONE}

    units = defaults / "known_units.csv"  # read by gamma only
    units.write_text(units.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert p.staleness() == {"alpha": Status.DONE, "beta": Status.DONE, "gamma": Status.STALE}


def test_source_change_changes_root_fingerprint(project: Project) -> None:
    src = project.root / "a.csv"
    src.write_text("x\n1\n", encoding="utf-8")
    project.set_sources([src])
    fp1 = project.fingerprint("alpha")
    src.write_text("x\n2\n", encoding="utf-8")
    project.set_sources([src])
    assert project.fingerprint("alpha") != fp1


def test_root_stage_receives_sources(project: Project) -> None:
    src = project.root / "a.csv"
    src.write_text("x\n1\n", encoding="utf-8")
    project.set_sources([src])
    assert project.run_stage("alpha").output["n_sources"].iloc[0] == 1


# ---- commit: log rows, checkpoints, manifest ----


def test_commit_logs_exactly_one_row_per_report_item(project: Project) -> None:
    res = project.run_stage("alpha")
    project.commit("alpha")
    rows = _by_stage(project.log_rows(), "alpha")
    auto = [r for r in rows if r.type == "auto-applied"]
    assert len(auto) == len(res.report) == 2
    assert [r.action for r in auto] == [i.action for i in res.report]
    assert auto[1].warnings == ("stub warning",)
    # persisted as JSONL
    log = project.root / "log" / "decisions.jsonl"
    assert len(log.read_text(encoding="utf-8").strip().splitlines()) == len(project.log_rows())


def test_commit_creates_checkpoint_with_complete_manifest(project: Project) -> None:
    ck = project.commit("alpha")
    assert ck.parent == project.root / "checkpoints"
    fp = project.fingerprint("alpha")
    assert ck.name.endswith(f"_alpha_{fp[:8]}")
    manifest = json.loads((ck / "manifest.json").read_text(encoding="utf-8"))
    for key in (
        "stage_fingerprint",
        "recipe_hash",
        "config_hash",
        "app_version",
        "python",
        "pandas",
        "numpy",
        "scikit-learn",
        "files",
    ):
        assert key in manifest
    assert manifest["stage_fingerprint"] == fp
    names = {f["file"] for f in manifest["files"]}
    assert any(n.startswith("evidence/") for n in names)
    for f in manifest["files"]:
        data = (ck / f["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == f["sha256"]
        assert {"rows", "cols"} <= set(f)
    # evidence file referenced from the log row
    rows = _by_stage(project.log_rows(), "alpha")
    assert rows[0].evidence_file is not None
    assert (project.root / rows[0].evidence_file).is_file()


def test_recommit_creates_new_dir_and_leaves_old_untouched(project: Project) -> None:
    first = project.commit("alpha")
    snapshot = {p: p.read_bytes() for p in first.rglob("*") if p.is_file()}
    second = project.commit("alpha")
    assert second != first and second.is_dir()
    assert {p: p.read_bytes() for p in first.rglob("*") if p.is_file()} == snapshot


def test_existing_checkpoint_dir_raises(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    import core.project as mod

    monkeypatch.setattr(mod, "_timestamp_for_dir", lambda: "20260101T000000.000")
    project.commit("alpha")
    with pytest.raises(FileExistsError):
        project.commit("alpha")


def test_commit_logs_gate_rows_user_and_default_accepted(project: Project) -> None:
    project.commit("alpha")
    project.set_params("beta", BetaParams(mode="drop"))
    project.commit("beta")
    gate_rows = [r for r in _by_stage(project.log_rows(), "beta") if r.action == "gate"]
    assert [(r.target, r.type, r.parameters["value"]) for r in gate_rows] == [
        ("beta.mode", "user", "drop")
    ]
    project.set_params(
        "beta", BetaParams(mode=AcceptDefault[str](accept_default=True, value="keep"))
    )
    project.commit("beta")
    gate_rows = [r for r in _by_stage(project.log_rows(), "beta") if r.action == "gate"]
    assert [r.type for r in gate_rows] == ["user", "default-accepted"]
    assert gate_rows[1].parameters["value"] == "keep"
    # report-item rows stay auto-applied
    assert {r.type for r in _by_stage(project.log_rows(), "beta") if r.action == "tag"} == {
        "auto-applied"
    }


def test_commit_with_unresolved_gate_raises(project: Project) -> None:
    with pytest.raises(UnresolvedGatesError):
        project.commit("beta")
    assert project.log_rows() == []


# ---- recipe, typed params, placeholder, stubs ----


def test_params_are_typed_after_recipe_round_trip(project: Project, defaults: Path) -> None:
    project.set_params("alpha", AlphaParams(factor=3))
    _accept_beta(project)
    path = project.write_recipe()
    assert path == project.root / "recipe.json"
    other = Project(project.root, stages=STUBS, defaults_dir=defaults)
    assert isinstance(other.params["alpha"], AlphaParams)
    assert other.params["alpha"].factor == 3
    assert isinstance(other.params["beta"], BetaParams)


def test_unresolved_gates_listed(project: Project) -> None:
    assert project.unresolved_gates() == ["beta.mode"]


def _old_recipe_with_drifted_default(root: Path) -> None:
    recipe = {
        "schema_version": 0,
        "settings": {"min_obs_per_cell": 3},
        "stages": {"beta": {"mode": {"accept_default": True, "value": "old"}}},
    }
    (root / "recipe.json").write_text(json.dumps(recipe), encoding="utf-8")


def _v0_to_v1(d: dict[str, object]) -> dict[str, object]:
    return {**d, "schema_version": 1}


def test_notices_are_held_in_memory_and_construction_writes_nothing(
    tmp_path: Path, defaults: Path
) -> None:
    (defaults / "PLACEHOLDER").write_text("", encoding="utf-8")
    root = tmp_path / "p2"
    root.mkdir()
    _old_recipe_with_drifted_default(root)
    before = _snapshot(tmp_path)
    first = Project(root, stages=STUBS, defaults_dir=defaults, recipe_registry={0: _v0_to_v1})
    Project(root, stages=STUBS, defaults_dir=defaults, recipe_registry={0: _v0_to_v1})
    assert _snapshot(tmp_path) == before
    assert not (root / "log").exists()
    actions = sorted(i.action for i in first.notices)
    assert actions == ["gate_default_changed", "migration", "placeholder_config"]


def test_commit_logs_migration_once_and_clears_it(tmp_path: Path, defaults: Path) -> None:
    root = tmp_path / "p2"
    root.mkdir()
    _old_recipe_with_drifted_default(root)
    p = Project(root, stages=STUBS, defaults_dir=defaults, recipe_registry={0: _v0_to_v1})
    p.commit("alpha")
    p.commit("alpha")
    migrations = [r for r in p.log_rows() if r.action == "migration"]
    assert len(migrations) == 1 and migrations[0].type == "migration"
    assert all(i.action != "migration" for i in p.notices)
    # a later session re-logs it until the recipe is rewritten (accepted, decisions.md)
    again = Project(root, stages=STUBS, defaults_dir=defaults, recipe_registry={0: _v0_to_v1})
    again.commit("alpha")
    assert len([r for r in again.log_rows() if r.action == "migration"]) == 2


def test_drift_notice_is_logged_only_with_owning_stage_commit(
    tmp_path: Path, defaults: Path
) -> None:
    root = tmp_path / "p2"
    root.mkdir()
    _old_recipe_with_drifted_default(root)
    p = Project(root, stages=STUBS, defaults_dir=defaults, recipe_registry={0: _v0_to_v1})
    p.commit("alpha")
    assert [r for r in p.log_rows() if r.action == "gate_default_changed"] == []
    p.commit("beta")
    drift = [r for r in p.log_rows() if r.action == "gate_default_changed"]
    assert len(drift) == 1 and drift[0].stage == "beta" and drift[0].target == "beta.mode"
    assert "old" in drift[0].warnings[0] and "keep" in drift[0].warnings[0]


def test_placeholder_notice_is_surfaced_and_logged_once_per_commit(
    tmp_path: Path, defaults: Path
) -> None:
    (defaults / "PLACEHOLDER").write_text("", encoding="utf-8")
    root = tmp_path / "p3"
    root.mkdir()
    p = Project(root, stages=STUBS, defaults_dir=defaults)
    assert p.placeholder is True
    assert any(i.action == "placeholder_config" and i.warning for i in p.notices)
    assert p.log_rows() == []
    p.commit("alpha")
    rows = [r for r in p.log_rows() if r.action == "placeholder_config"]
    assert len(rows) == 1 and rows[0].warnings
    p.commit("alpha")
    assert len([r for r in p.log_rows() if r.action == "placeholder_config"]) == 2


def test_no_placeholder_when_marker_absent(project: Project) -> None:
    assert project.placeholder is False


def test_needs_attention_is_empty_stub(project: Project) -> None:
    assert project.needs_attention() == []


def test_unknown_stage_name_raises(project: Project) -> None:
    with pytest.raises(KeyError):
        project.run_stage("nope")


# ---- dependencies, duplicate names, log errors ----


def test_commit_refuses_when_dependency_has_no_head(project: Project) -> None:
    _accept_beta(project)
    with pytest.raises(DependencyNotCommittedError) as exc:
        project.commit("beta")
    assert (exc.value.stage, exc.value.dependency) == ("beta", "alpha")
    assert (
        "beta" in str(exc.value)
        and "alpha" in str(exc.value)
        and "Commit 'alpha' first" in str(exc.value)
    )
    assert project.log_rows() == [] and not (project.root / "checkpoints").exists()


def test_commit_refuses_when_dependency_head_is_stale(project: Project, defaults: Path) -> None:
    _accept_beta(project)
    project.commit("alpha")
    tokens = defaults / "tokens.json"  # alpha reads it: its head is now stale
    tokens.write_text(tokens.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(DependencyNotCommittedError) as exc:
        project.commit("beta")
    assert exc.value.status == "stale" and "stale" in str(exc.value)
    project.commit("alpha")
    project.commit("beta")  # fine once alpha is recommitted


def test_duplicate_stage_names_raise(tmp_path: Path, defaults: Path) -> None:
    root = tmp_path / "dup"
    root.mkdir()
    with pytest.raises(ValueError, match="alpha"):
        Project(root, stages=(Alpha, Alpha), defaults_dir=defaults)


def test_damaged_log_error_names_file_and_line(project: Project) -> None:
    project.commit("alpha")
    log = project.root / "log" / "decisions.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines()
    lines.insert(1, "{not json")
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        project.log_rows()
    assert "decisions.jsonl" in str(exc.value) and "line 2" in str(exc.value)


def test_current_recipe_matches_build_recipe(project: Project) -> None:
    from core.recipe import build_recipe

    _accept_beta(project)
    expected = build_recipe(
        project_root=project.root,
        source_paths=[],
        config_snapshot=project.current_recipe().config,
        settings=project.current_recipe().settings,
        stages=dict(project.params),
    )
    assert project.current_recipe() == expected
