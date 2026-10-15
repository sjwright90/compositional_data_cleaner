from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core.audit import row_from_json
from core.recipe import RecipeSettings, build_recipe, recipe_to_json
from core.run import main
from tests import stub_stages
from tests.fixtures.generate import generate
from tests.stub_stages import STUBS

FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"


@pytest.fixture(autouse=True)
def _clear_calls() -> None:
    stub_stages.CALLS.clear()


@pytest.fixture
def defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "defaults"
    shutil.copytree(FIXTURE_CONFIG, d)
    import core.run as run_mod

    monkeypatch.setattr(run_mod, "DEFAULT_CONFIG_DIR", d)
    return d


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    generate("project_basic", 20240101, tmp_path / "gen")
    root = tmp_path / "gen" / "project_basic"
    sources = sorted(p for p in root.iterdir() if p.suffix in {".csv", ".xlsx"})
    recipe = build_recipe(
        project_root=root,
        source_paths=sources,
        config_snapshot={},
        settings=RecipeSettings(min_obs_per_cell=3),
        stages={},
    )
    (root / "recipe.json").write_text(recipe_to_json(recipe), encoding="utf-8")
    return root


def _log(root: Path) -> list:
    path = root / "log" / "decisions.jsonl"
    return [row_from_json(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _checkpoints(root: Path, stage: str) -> list[Path]:
    base = root / "checkpoints"
    return sorted(base.glob(f"*_{stage}_*")) if base.is_dir() else []


def test_unresolved_gates_exit_2_and_listed(
    proj: Path, defaults: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--project", str(proj)], stages=STUBS)
    out = capsys.readouterr().out
    assert code == 2
    assert "beta.mode" in out
    assert _checkpoints(proj, "beta") == []


def test_accept_defaults_exit_0_with_warning_and_explicit_entries(
    proj: Path, defaults: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--project", str(proj), "--accept-defaults"], stages=STUBS)
    cap = capsys.readouterr()
    assert code == 0
    assert "accept-defaults" in (cap.out + cap.err).lower()
    recipe = json.loads((proj / "recipe.json").read_text(encoding="utf-8"))
    assert recipe["stages"]["beta"]["mode"] == {"accept_default": True, "value": "keep"}
    types = {(r.action, r.type) for r in _log(proj) if r.stage == "beta"}
    assert ("gate", "default-accepted") in types
    assert len(_checkpoints(proj, "alpha")) == 1
    assert len(_checkpoints(proj, "beta")) == 1


def test_second_run_is_unchanged_and_not_recommitted(
    proj: Path, defaults: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--project", str(proj), "--accept-defaults"], stages=STUBS) == 0
    capsys.readouterr()
    assert main(["--project", str(proj)], stages=STUBS) == 0
    out = capsys.readouterr().out
    assert "alpha: unchanged, not recommitted" in out
    assert "beta: unchanged, not recommitted" in out
    assert len(_checkpoints(proj, "alpha")) == 1
    assert len(_checkpoints(proj, "beta")) == 1


def test_recipe_option_writes_output_recipe_there(
    proj: Path, defaults: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out_recipe.json"
    code = main(
        ["--project", str(proj), "--recipe", str(proj / "recipe.json"), "--accept-defaults"],
        stages=STUBS,
    )
    assert code == 0
    alt = tmp_path / "alt.json"
    shutil.copy(proj / "recipe.json", alt)
    assert main(["--project", str(proj), "--recipe", str(alt)], stages=STUBS) == 0
    assert not out.exists()


def test_placeholder_line_printed(
    proj: Path, defaults: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (defaults / "PLACEHOLDER").write_text("", encoding="utf-8")
    main(["--project", str(proj), "--accept-defaults"], stages=STUBS)
    assert "using placeholder configuration" in capsys.readouterr().out


def test_no_placeholder_line_without_marker(
    proj: Path, defaults: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--project", str(proj), "--accept-defaults"], stages=STUBS)
    assert "using placeholder configuration" not in capsys.readouterr().out


def test_bad_recipe_exit_1_no_traceback(
    proj: Path, defaults: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (proj / "recipe.json").write_text("{not json", encoding="utf-8")
    code = main(["--project", str(proj)], stages=STUBS)
    cap = capsys.readouterr()
    assert code == 1
    assert "Traceback" not in cap.out + cap.err
    assert (cap.out + cap.err).strip()


def test_newer_recipe_version_exit_1(
    proj: Path, defaults: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = json.loads((proj / "recipe.json").read_text(encoding="utf-8"))
    data["schema_version"] = 999
    (proj / "recipe.json").write_text(json.dumps(data), encoding="utf-8")
    assert main(["--project", str(proj)], stages=STUBS) == 1
    assert "newer version" in (capsys.readouterr().err + "")


def test_missing_project_exit_1(tmp_path: Path, defaults: Path) -> None:
    assert main(["--project", str(tmp_path / "nope")], stages=STUBS) == 1


def test_help_documents_exit_codes() -> None:
    res = subprocess.run(
        [sys.executable, "-m", "core.run", "--help"], capture_output=True, text=True, check=False
    )
    assert res.returncode == 0
    for needle in ("0", "1", "2", "unresolved", "tests only (A3)"):
        assert needle in res.stdout
