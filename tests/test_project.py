from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from core import project as project_module
from core.audit import LogRow
from core.ingest import UnsupportedSourceError
from core.pipeline import Status, canonical_json
from core.project import (
    BlockingItemsError,
    DependencyNotCommittedError,
    Project,
    SourceChangedError,
    SourceMissingError,
    UnresolvedGatesError,
)
from core.recipe import AcceptDefault, recipe_hash
from tests import stub_stages
from tests.stub_stages import (
    STUBS,
    STUBS_WITH_GAMMA,
    Alpha,
    AlphaParams,
    Beta,
    BetaOut,
    BetaParams,
    Delta,
    DeltaParams,
    Entry,
    Eta,
    EtaParams,
    Keys,
    KeysParams,
    MapEntry,
    SheetDecision,
    Zeta,
    ZetaParams,
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


def _state_snapshot(root: Path) -> dict[str, bytes]:
    """Every file under `root` except the disposable memo `cache/<stage>/...` (write-free
    definition, docs/decisions.md). `cache/heads.json` is state and stays in the snapshot."""
    return {
        k: v
        for k, v in _snapshot(root).items()
        if "cache" not in k.split("/")[:-1] or k.endswith("cache/heads.json")
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
    before = _state_snapshot(tmp_path)
    first = Project(root, stages=STUBS, defaults_dir=defaults, recipe_registry={0: _v0_to_v1})
    Project(root, stages=STUBS, defaults_dir=defaults, recipe_registry={0: _v0_to_v1})
    assert _state_snapshot(tmp_path) == before
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


# ---- import_sources (A15, A17) ----


def _incoming(tmp_path: Path, name: str, text: str, folder: str = "incoming") -> Path:
    p = tmp_path / folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


def _recipe_sources(root: Path) -> list[dict[str, str]]:
    data = json.loads((root / "recipe.json").read_text(encoding="utf-8"))
    return list(data["sources"])


def test_import_copies_hashes_and_lists_in_recipe(project: Project, tmp_path: Path) -> None:
    src = _incoming(tmp_path, "a.csv", "x\n1\n")
    (item,) = project.import_sources([src])
    copy = project.root / "sources" / "a.csv"
    assert copy.read_bytes() == src.read_bytes()
    assert item.action == "source_imported"
    assert item.warning is None
    assert _recipe_sources(project.root) == [
        {"path": "sources/a.csv", "sha256": hashlib.sha256(src.read_bytes()).hexdigest()}
    ]
    assert not list((project.root / "sources").glob(".*.tmp"))


def test_import_same_file_twice_is_one_copy_and_info_item(project: Project, tmp_path: Path) -> None:
    src = _incoming(tmp_path, "a.csv", "x\n1\n")
    project.import_sources([src])
    (item,) = project.import_sources([src])
    assert item.action == "source_unchanged"
    assert item.warning is None
    assert [p.name for p in (project.root / "sources").iterdir()] == ["a.csv"]
    assert len(_recipe_sources(project.root)) == 1
    assert len(project.log_rows()) == 1  # a pure no-op writes no row


def test_same_name_different_content_gets_numbered_copies(project: Project, tmp_path: Path) -> None:
    first = _incoming(tmp_path, "a.csv", "x\n1\n")
    second = _incoming(tmp_path, "a.csv", "x\n2\n", folder="other")
    third = _incoming(tmp_path, "a.csv", "x\n3\n", folder="third")
    project.import_sources([first])
    (item2,) = project.import_sources([second])
    (item3,) = project.import_sources([third])
    src_dir = project.root / "sources"
    assert (src_dir / "a.csv").read_text(encoding="utf-8") == "x\n1\n"
    assert (src_dir / "a_2.csv").read_text(encoding="utf-8") == "x\n2\n"
    assert (src_dir / "a_3.csv").read_text(encoding="utf-8") == "x\n3\n"
    assert item2.warning is not None
    assert "'a.csv'" in item2.warning and "'a_2.csv'" in item2.warning
    assert item3.warning is not None and "'a_3.csv'" in item3.warning
    (again,) = project.import_sources([second])  # matches a_2 by content: no a_4
    assert again.action == "source_unchanged"
    assert sorted(p.name for p in src_dir.iterdir()) == ["a.csv", "a_2.csv", "a_3.csv"]


def test_recipe_is_machine_independent(tmp_path: Path, defaults: Path) -> None:
    hashes = []
    for place in ("one", "deeper/two"):
        root = tmp_path / place / "proj"
        root.mkdir(parents=True)
        p = Project(root, stages=STUBS, defaults_dir=defaults)
        p.import_sources([_incoming(tmp_path, "a.csv", "x\n1\n", folder=f"in_{place}")])
        text = (root / "recipe.json").read_text(encoding="utf-8")
        assert str(tmp_path) not in text and "\\" not in text
        hashes.append(recipe_hash(p.current_recipe()))
    assert hashes[0] == hashes[1]


def test_import_logs_one_row_per_file_with_original_path(project: Project, tmp_path: Path) -> None:
    a = _incoming(tmp_path, "a.csv", "x\n1\n")
    b = _incoming(tmp_path, "b.csv", "x\n2\n")
    project.import_sources([a, b])
    rows = project.log_rows()
    assert [r.target for r in rows] == ["sources/a.csv", "sources/b.csv"]
    assert [r.parameters["original_path"] for r in rows] == [str(a.resolve()), str(b.resolve())]
    assert all(r.stage == "import" and r.action == "import_source" and r.timestamp for r in rows)


def test_construct_run_and_preview_change_nothing(
    project: Project, tmp_path: Path, defaults: Path
) -> None:
    project.import_sources([_incoming(tmp_path, "a.csv", "x\n1\n")])
    before = _state_snapshot(project.root)
    fresh = Project(project.root, stages=STUBS, defaults_dir=defaults)
    assert _state_snapshot(project.root) == before
    fresh.preview("alpha", AlphaParams(factor=3))
    assert _state_snapshot(project.root) == before


def test_fresh_project_lists_imported_source_without_memory(
    project: Project, tmp_path: Path, defaults: Path
) -> None:
    project.import_sources([_incoming(tmp_path, "a.csv", "x\n1\n")])
    fresh = Project(project.root, stages=STUBS, defaults_dir=defaults)
    assert [s.path for s in fresh.current_recipe().sources] == ["sources/a.csv"]


def test_reimport_of_unlisted_copy_readds_it(
    project: Project, tmp_path: Path, defaults: Path
) -> None:
    src = _incoming(tmp_path, "a.csv", "x\n1\n")
    project.import_sources([src])
    (project.root / "recipe.json").unlink()  # crash after copy, before the recipe write
    (project.root / "log" / "decisions.jsonl").unlink()
    fresh = Project(project.root, stages=STUBS, defaults_dir=defaults)
    assert fresh.current_recipe().sources == []
    (item,) = fresh.import_sources([src])
    assert item.action == "source_readded"
    assert item.warning is None
    assert [p.name for p in (project.root / "sources").iterdir()] == ["a.csv"]
    assert [s.path for s in fresh.current_recipe().sources] == ["sources/a.csv"]
    assert len(fresh.log_rows()) == 1


@pytest.mark.parametrize("bad", ["old.xls", "OLD.XLS", "data.parquet", "notes.txt"])
def test_unsupported_extension_refuses_whole_import(
    project: Project, tmp_path: Path, bad: str
) -> None:
    good = _incoming(tmp_path, "a.csv", "x\n1\n")
    unsupported = _incoming(tmp_path, bad, "junk")
    before = _state_snapshot(project.root)
    with pytest.raises(UnsupportedSourceError) as exc:
        project.import_sources([good, unsupported])
    assert str(unsupported) in str(exc.value)
    assert "save it as .xlsx or .csv" in str(exc.value)
    assert str(good) not in str(exc.value)
    assert _state_snapshot(project.root) == before
    assert project.current_recipe().sources == []


def test_missing_input_file_is_plain_and_changes_nothing(project: Project, tmp_path: Path) -> None:
    before = _state_snapshot(project.root)
    with pytest.raises(FileNotFoundError, match="could not be found"):
        project.import_sources([tmp_path / "nope.csv"])
    assert _state_snapshot(project.root) == before


# ---- source verification (A15) ----


def test_changed_source_blocks_root_stage_until_restored(project: Project, tmp_path: Path) -> None:
    project.import_sources([_incoming(tmp_path, "a.csv", "x\n1\n")])
    _accept_beta(project)
    project.run_stage("alpha")  # now cached and memoised
    copy = project.root / "sources" / "a.csv"
    original = copy.read_bytes()
    copy.write_bytes(b"x\n999\n")
    for call in (
        lambda: project.run_stage("alpha"),
        lambda: project.preview("alpha", AlphaParams()),
        lambda: project.run_stage("beta"),
    ):
        with pytest.raises(SourceChangedError, match=r"sources/a\.csv.*changed after") as exc:
            call()
        assert hashlib.sha256(original).hexdigest()[:12] in str(exc.value)
    copy.write_bytes(original)
    assert project.run_stage("alpha").output["n_sources"].iloc[0] == 1


def test_missing_source_copy_has_its_own_message(project: Project, tmp_path: Path) -> None:
    project.import_sources([_incoming(tmp_path, "a.csv", "x\n1\n")])
    (project.root / "sources" / "a.csv").unlink()
    with pytest.raises(SourceMissingError, match=r"sources/a\.csv.*missing"):
        project.run_stage("alpha")


# ---- golden root fingerprint payload ----


def test_root_fingerprint_payload_shape_is_pinned(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _incoming(tmp_path, "a.csv", "x\n1\n")
    b = _incoming(tmp_path, "b.csv", "x\n2\n")
    project.import_sources([b, a])
    seen: dict[str, str] = {}
    real = project_module._fingerprint

    def spy(stage: Any, payload: Any, deps: Any, cfg: str) -> str:
        if stage.name == "alpha":
            seen["payload"] = canonical_json(payload)
        return real(stage, payload, deps, cfg)

    monkeypatch.setattr(project_module, "_fingerprint", spy)
    project.fingerprint("alpha")
    ha, hb = (hashlib.sha256(p.read_bytes()).hexdigest() for p in (a, b))
    assert seen["payload"] == (
        f'{{"params":{{"factor":1}},"sources":[["sources/a.csv","{ha}"],["sources/b.csv","{hb}"]]}}'
    )


def test_import_persists_only_sources_not_in_memory_edits(project: Project, tmp_path: Path) -> None:
    project.write_recipe()
    project.set_params("alpha", AlphaParams(factor=7))
    project.set_params("beta", BetaParams(mode="keep", tag="edited"))
    project.import_sources([_incoming(tmp_path, "a.csv", "x\n1\n")])
    disk = json.loads((project.root / "recipe.json").read_text(encoding="utf-8"))
    assert [s["path"] for s in disk["sources"]] == ["sources/a.csv"]
    assert disk["stages"]["alpha"] == {"factor": 1}
    assert disk["stages"]["beta"] == {"mode": None, "tag": "t"}
    memory = project.current_recipe()
    assert [s.path for s in memory.sources] == ["sources/a.csv"]
    assert memory.stages["alpha"] == {"factor": 7}
    assert memory.stages["beta"] == {"mode": "keep", "tag": "edited"}


def test_import_without_recipe_file_starts_a_default_one(project: Project, tmp_path: Path) -> None:
    project.set_params("alpha", AlphaParams(factor=7))
    assert not (project.root / "recipe.json").exists()
    project.import_sources([_incoming(tmp_path, "a.csv", "x\n1\n")])
    disk = json.loads((project.root / "recipe.json").read_text(encoding="utf-8"))
    assert disk["stages"]["alpha"] == {"factor": 1}
    assert project.params["alpha"] == AlphaParams(factor=7)


def test_readd_logs_one_row_and_true_noop_logs_none(project: Project, tmp_path: Path) -> None:
    src = _incoming(tmp_path, "a.csv", "x\n1\n")
    project.import_sources([src])
    assert len(project.log_rows()) == 1
    project.import_sources([src])  # true no-op: nothing changes
    assert len(project.log_rows()) == 1
    (project.root / "recipe.json").unlink()  # copy is in sources/ but not in the recipe
    fresh = Project(project.root, stages=STUBS, defaults_dir=project._layers[0])
    (item,) = fresh.import_sources([src])
    assert item.action == "source_readded"
    rows = fresh.log_rows()
    assert len(rows) == 2
    assert rows[-1].parameters["outcome"] == "source_readded"
    fresh.import_sources([src])  # now a no-op again
    assert len(fresh.log_rows()) == 2


# ---- T11a plumbing: gates in collections, suggest, sync_params, blocking, usage ----


def _mk(tmp_path: Path, defaults: Path, stages: Any) -> Project:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    return Project(root, stages=stages, defaults_dir=defaults)


def _zeta(p: Project, name: str = "zeta") -> ZetaParams:
    params = p.params[name]
    assert isinstance(params, ZetaParams)
    return params


def _entries(params: Any) -> dict[str, Entry]:
    assert isinstance(params, ZetaParams)
    return params.entries


def _decide(p: Project, **decisions: str) -> None:
    entries = {k: Entry(decision=v) for k, v in decisions.items()}
    p.set_params("zeta", ZetaParams(entries=entries))


def test_collection_gates_listed_accepted_and_logged_per_indexed_path(
    tmp_path: Path, defaults: Path
) -> None:
    p = _mk(tmp_path, defaults, (Delta,))
    p.set_params(
        "delta",
        DeltaParams(
            sheets=[SheetDecision(), SheetDecision(layout="long")],
            maps={"mn": MapEntry()},
        ),
    )
    assert p.unresolved_gates() == ["delta.maps[mn].unit", "delta.sheets[0].layout"]
    with pytest.raises(UnresolvedGatesError) as exc:
        p.commit("delta")
    assert exc.value.fields == ["delta.maps[mn].unit", "delta.sheets[0].layout"]
    assert p.accept_defaults() == ["delta.maps[mn].unit", "delta.sheets[0].layout"]
    p.commit("delta")
    gates = {r.target: r.type for r in _by_stage(p.log_rows(), "delta") if r.action == "gate"}
    assert gates == {
        "delta.maps[mn].unit": "default-accepted",
        "delta.sheets[0].layout": "default-accepted",
        "delta.sheets[1].layout": "user",
    }


def test_collection_gate_drift_names_indexed_path(tmp_path: Path, defaults: Path) -> None:
    p = _mk(tmp_path, defaults, (Delta,))
    p.set_params(
        "delta",
        DeltaParams(sheets=[SheetDecision(layout=AcceptDefault(accept_default=True, value="old"))]),
    )
    p.write_recipe()
    again = _mk(tmp_path, defaults, (Delta,))
    assert [n.target for n in again.notices if n.action == "gate_default_changed"] == [
        "delta.sheets[0].layout"
    ]


def test_sync_params_adds_entries_with_unresolved_gates(tmp_path: Path, defaults: Path) -> None:
    p = _mk(tmp_path, defaults, (Keys, Zeta))
    assert p.unresolved_gates() == ["zeta.entries[a].decision", "zeta.entries[b].decision"]
    assert sorted(_zeta(p).entries) == ["a", "b"]
    assert p.current_recipe().stages["zeta"]["entries"].keys() == {"a", "b"}


def test_sync_keeps_decisions_and_syncing_twice_equals_once(tmp_path: Path, defaults: Path) -> None:
    p = _mk(tmp_path, defaults, (Keys, Zeta))
    _decide(p, a="drop")
    p.sync_params()
    once = p.params["zeta"]
    p.sync_params()
    assert p.params["zeta"] == once
    assert _entries(once)["a"].decision == "drop"
    assert _entries(once)["b"].decision is None


def test_decided_orphan_is_kept_excluded_reported_and_ignored_by_run(
    tmp_path: Path, defaults: Path
) -> None:
    p = _mk(tmp_path, defaults, (Keys, Zeta))
    _decide(p, a="keep", b="drop")
    p.set_params("keys", KeysParams(keys=["a"]))
    assert p.unresolved_gates() == []
    assert sorted(_zeta(p).entries) == ["a", "b"]  # orphan b kept in the recipe
    result = p.run_stage("zeta")
    assert list(result.output["key"]) == ["a"]
    orphan_items = [i for i in result.report if i.action == "orphan_decisions"]
    assert len(orphan_items) == 1
    assert orphan_items[0].count == 1 and orphan_items[0].params["keys"] == ["entries[b]"]
    p.commit("keys")
    p.commit("zeta")
    gate_targets = [r.target for r in _by_stage(p.log_rows(), "zeta") if r.action == "gate"]
    assert gate_targets == ["zeta.entries[a].decision"]


def test_undecided_orphan_is_dropped(tmp_path: Path, defaults: Path) -> None:
    p = _mk(tmp_path, defaults, (Keys, Zeta))
    _decide(p, a="keep")
    p.sync_params()
    assert sorted(_zeta(p).entries) == ["a", "b"]  # b added, undecided
    p.set_params("keys", KeysParams(keys=["a"]))
    p.sync_params()
    assert sorted(_zeta(p).entries) == ["a"]


def test_reappearing_key_reuses_old_decision_and_leaves_orphans(
    tmp_path: Path, defaults: Path
) -> None:
    p = _mk(tmp_path, defaults, (Keys, Zeta))
    _decide(p, a="keep", b="drop")
    p.set_params("keys", KeysParams(keys=["a"]))
    assert [i.action for i in p.run_stage("zeta").report if i.action == "orphan_decisions"]
    p.set_params("keys", KeysParams(keys=["a", "b"]))
    result = p.run_stage("zeta")
    assert dict(zip(result.output["key"], result.output["decision"], strict=True)) == {
        "a": "keep",
        "b": "drop",
    }
    assert not [i for i in result.report if i.action == "orphan_decisions"]


def test_dependent_entries_appear_only_when_upstream_is_runnable(
    tmp_path: Path, defaults: Path
) -> None:
    from typing import ClassVar

    from core.recipe import Gate, gate_field

    class GKParams(KeysParams):
        ok: Gate[str] = gate_field("y")

    class GatedKeys(Keys):
        name: ClassVar[str] = "gkeys"
        Params: ClassVar[type[Any]] = GKParams

    class DownZeta(Zeta):
        name: ClassVar[str] = "dzeta"
        depends_on: ClassVar[tuple[str, ...]] = ("gkeys",)

    p = _mk(tmp_path, defaults, (GatedKeys, DownZeta))
    assert p.unresolved_gates() == ["gkeys.ok"]
    assert _zeta(p, "dzeta").entries == {}
    assert p.staleness()["dzeta"] is Status.BLOCKED
    p.set_params("gkeys", GKParams(ok="y"))
    assert p.unresolved_gates() == ["dzeta.entries[a].decision", "dzeta.entries[b].decision"]
    assert p.staleness()["dzeta"] is Status.BLOCKED  # upstream not committed yet


def test_suggestions_work_while_gates_are_unresolved_and_write_nothing(
    tmp_path: Path, defaults: Path
) -> None:
    p = _mk(tmp_path, defaults, (Keys, Zeta))
    before = _state_snapshot(p.root)  # write-free: everything but the memo cache/<stage>/
    res = p.suggestions("zeta")
    assert res.values == {"entries[a].decision": "s_a:1", "entries[b].decision": "s_b:1"}
    assert [(i.action, i.target, i.params["value"]) for i in res.report] == [
        ("gate_suggestion", "zeta.entries[a].decision", "s_a:1"),
        ("gate_suggestion", "zeta.entries[b].decision", "s_b:1"),
    ]
    assert _state_snapshot(p.root) == before
    assert p.unresolved_gates() == ["zeta.entries[a].decision", "zeta.entries[b].decision"]
    assert _state_snapshot(p.root) == before
    _mk(tmp_path, defaults, (Keys, Zeta))  # construction
    assert _state_snapshot(p.root) == before
    with pytest.raises(UnresolvedGatesError):
        p.run_stage("zeta")
    with pytest.raises(UnresolvedGatesError):
        p.preview("zeta", p.params["zeta"])
    p.commit("keys")
    with pytest.raises(UnresolvedGatesError):
        p.commit("zeta")


def test_suggestions_raise_for_stage_without_suggest_when_upstream_not_runnable(
    tmp_path: Path, defaults: Path
) -> None:
    from typing import ClassVar

    class Beta2(Beta):
        name: ClassVar[str] = "beta2"
        depends_on: ClassVar[tuple[str, ...]] = ("beta",)

    p = _mk(tmp_path, defaults, (Alpha, Beta, Beta2))
    with pytest.raises(UnresolvedGatesError) as exc:
        p.suggestions("beta2")  # beta.mode is unresolved
    assert exc.value.fields == ["beta.mode"]


def test_suggestions_for_suggest_stage_without_inputs_are_empty(
    tmp_path: Path, defaults: Path
) -> None:
    from typing import ClassVar

    from core.recipe import Gate, gate_field

    class GKParams(KeysParams):
        ok: Gate[str] = gate_field("y")

    class GatedKeys(Keys):
        name: ClassVar[str] = "gkeys"
        Params: ClassVar[type[Any]] = GKParams

    class DownZeta(Zeta):
        name: ClassVar[str] = "dzeta"
        depends_on: ClassVar[tuple[str, ...]] = ("gkeys",)

    p = _mk(tmp_path, defaults, (GatedKeys, DownZeta))
    res = p.suggestions("dzeta")
    assert res.values == {}
    assert [(i.action, i.target, i.count, list(i.params["gates"])) for i in res.report] == [
        ("upstream_not_runnable", "gkeys", 1, ["gkeys.ok"])
    ]
    p.set_params("gkeys", GKParams(ok="y"))
    assert p.suggestions("dzeta").report == () or all(
        i.action == "gate_suggestion" for i in p.suggestions("dzeta").report
    )


def test_accept_defaults_stores_resolved_suggested_value_not_a_sentinel(
    tmp_path: Path, defaults: Path
) -> None:
    p = _mk(tmp_path, defaults, (Keys, Zeta))
    assert p.accept_defaults() == ["zeta.entries[a].decision", "zeta.entries[b].decision"]
    entries = _zeta(p).entries
    assert entries["a"].decision == AcceptDefault(accept_default=True, value="s_a:1")
    p.commit("keys")
    p.commit("zeta")
    rows = [r for r in _by_stage(p.log_rows(), "zeta") if r.action == "gate"]
    assert {r.type for r in rows} == {"default-accepted"}


def test_suggested_gate_without_suggestion_is_not_accepted_and_stays_pending(
    tmp_path: Path, defaults: Path
) -> None:
    p = _mk(tmp_path, defaults, (Keys, Zeta))
    p.set_params("keys", KeysParams(keys=["a", "n1"]))  # "n..." keys get no suggestion
    assert p.accept_defaults() == ["zeta.entries[a].decision"]
    assert _zeta(p).entries["n1"].decision is None
    assert p.unresolved_gates() == ["zeta.entries[n1].decision"]


def test_changed_upstream_data_changes_suggestion_and_gives_drift_notice(
    tmp_path: Path, defaults: Path
) -> None:
    p = _mk(tmp_path, defaults, (Keys, Zeta))
    p.accept_defaults()
    p.commit("keys")
    p.commit("zeta")
    p.set_params("keys", KeysParams(n=2))
    p.sync_params()
    drift = [n for n in p.notices if n.action == "gate_default_changed"]
    assert sorted(n.target for n in drift) == [
        "zeta.entries[a].decision",
        "zeta.entries[b].decision",
    ]
    assert drift[0].params["current"].endswith(":2")
    p.sync_params()  # not duplicated
    assert len([n for n in p.notices if n.action == "gate_default_changed"]) == 2
    p.commit("keys")
    p.commit("zeta")
    logged = [r for r in _by_stage(p.log_rows(), "zeta") if r.action == "gate_default_changed"]
    assert len(logged) == 2


def test_blocking_item_is_returned_by_run_stage_and_refused_by_commit(
    tmp_path: Path, defaults: Path
) -> None:
    p = _mk(tmp_path, defaults, (Eta,))
    result = p.run_stage("eta")
    assert [i.warning for i in result.report if i.blocking] == ["stub blocking problem"]
    before = _state_snapshot(p.root)
    with pytest.raises(BlockingItemsError) as exc:
        p.commit("eta")
    assert "stub blocking problem" in str(exc.value)
    assert _state_snapshot(p.root) == before  # no checkpoint, log row or head
    assert not (p.root / "cache" / "heads.json").exists()
    assert p.heads == {}


def test_blocking_flag_survives_the_cache(tmp_path: Path, defaults: Path) -> None:
    p = _mk(tmp_path, defaults, (Eta,))
    p.run_stage("eta")
    again = _mk(tmp_path, defaults, (Eta,))
    items = [i for i in again.run_stage("eta").report if i.action == "bad_rows"]
    assert items and items[0].blocking


def _config_usage(p: Project) -> dict[str, Any]:
    cfg = p.current_recipe().config
    return {
        "analytes": [a["key"] for a in cfg["analytes"]],
        "units": [u["unit"] for u in cfg["units"]],
        "thresholds": sorted(cfg["thresholds"]),
        "tokens": sorted(cfg["tokens"]),
    }


_ETA_USAGE = {
    "analytes": ["mn"],
    "units": ["mg/L"],
    "thresholds": ["min_obs_per_cell"],
    "tokens": ["no_result_tokens"],
}


def test_recipe_snapshot_holds_exactly_the_committed_lookups(
    tmp_path: Path, defaults: Path
) -> None:
    p = _mk(tmp_path, defaults, (Eta,))
    p.set_params("eta", EtaParams(block=False))
    empty: dict[str, list[str]] = {"analytes": [], "units": [], "thresholds": [], "tokens": []}
    assert _config_usage(p) == empty
    p.preview("eta", EtaParams(block=False))
    p.run_stage("eta")  # uncommitted runs do not change the recipe
    assert _config_usage(p) == empty
    h0 = recipe_hash(p.current_recipe())
    p.commit("eta")
    assert _config_usage(p) == _ETA_USAGE
    assert recipe_hash(p.current_recipe()) != h0
    manifest = json.loads(
        next((p.root / "checkpoints").glob("*_eta_*")).joinpath("manifest.json").read_text("utf-8")
    )
    assert manifest["vocabulary_usage"]["analytes"] == ["mn"]
    assert manifest["recipe_hash"] == recipe_hash(p.current_recipe())


def test_snapshot_survives_fresh_project_and_deleted_cache(tmp_path: Path, defaults: Path) -> None:
    p = _mk(tmp_path, defaults, (Eta,))
    p.set_params("eta", EtaParams(block=False))
    p.commit("eta")
    p.write_recipe()
    expected = recipe_hash(p.current_recipe())
    shutil.rmtree(p.root / "cache" / "eta")
    fresh = _mk(tmp_path, defaults, (Eta,))
    assert _config_usage(fresh) == _ETA_USAGE
    assert recipe_hash(fresh.current_recipe()) == expected


def test_cache_hit_still_reports_usage_for_the_commit(tmp_path: Path, defaults: Path) -> None:
    p = _mk(tmp_path, defaults, (Eta,))
    p.set_params("eta", EtaParams(block=False))
    p.run_stage("eta")  # fills the cache
    p.write_recipe()
    fresh = _mk(tmp_path, defaults, (Eta,))
    stub_stages.CALLS.clear()
    fresh.commit("eta")
    assert stub_stages.CALLS == []  # served from the cache
    assert _config_usage(fresh) == _ETA_USAGE


def test_cache_entry_without_usage_is_a_miss_with_warning(tmp_path: Path, defaults: Path) -> None:
    p = _mk(tmp_path, defaults, (Eta,))
    p.set_params("eta", EtaParams(block=False))
    p.run_stage("eta")
    p.write_recipe()
    meta = next((p.root / "cache" / "eta").glob("*/meta.json"))
    data = json.loads(meta.read_text("utf-8"))
    del data["usage"]
    meta.write_text(json.dumps(data), encoding="utf-8")
    fresh = _mk(tmp_path, defaults, (Eta,))
    stub_stages.CALLS.clear()
    report = fresh.run_stage("eta").report
    assert stub_stages.CALLS == ["eta"]
    assert [i.action for i in report].count("cache_miss_unreadable") == 1
