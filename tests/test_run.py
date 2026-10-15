from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from core.audit import row_from_json
from core.config import ConfigError
from core.pipeline import FileLockedError, StageResult
from core.project import DependencyNotCommittedError
from core.recipe import RecipeSettings, build_recipe, recipe_to_json
from core.run import main
from tests import stub_stages
from tests.fixtures.generate import generate
from tests.stub_stages import STUBS, Alpha

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


def test_recipe_option_reads_and_writes_the_given_file(proj: Path, defaults: Path) -> None:
    original = (proj / "recipe.json").read_bytes()
    alt = proj.parent / "alt_recipe.json"
    shutil.copy(proj / "recipe.json", alt)
    code = main(["--project", str(proj), "--recipe", str(alt), "--accept-defaults"], stages=STUBS)
    assert code == 0
    written = json.loads(alt.read_text(encoding="utf-8"))
    assert written["stages"]["beta"]["mode"] == {"accept_default": True, "value": "keep"}
    assert (proj / "recipe.json").read_bytes() == original


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
    for code, word in (("0", "success"), ("1", "error"), ("2", "unresolved gates")):
        assert re.search(rf"^\s+{code}\s+{word}\b", res.stdout, re.MULTILINE), (code, word)
    assert "tests only (A3)" in res.stdout
    assert "blocking problems" in res.stdout


def test_second_run_adds_no_log_rows(proj: Path, defaults: Path) -> None:
    (defaults / "PLACEHOLDER").write_text("", encoding="utf-8")
    assert main(["--project", str(proj), "--accept-defaults"], stages=STUBS) == 0
    log = proj / "log" / "decisions.jsonl"
    first = log.read_bytes()
    assert any(r.action == "placeholder_config" for r in _log(proj))
    assert main(["--project", str(proj)], stages=STUBS) == 0
    assert log.read_bytes() == first


def test_invalid_recipe_field_prints_plain_per_field_message(
    proj: Path, defaults: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = json.loads((proj / "recipe.json").read_text(encoding="utf-8"))
    data["stages"] = {"alpha": {"factor": "lots"}}
    data["settings"] = {"min_obs_per_cell": "many"}
    (proj / "recipe.json").write_text(json.dumps(data), encoding="utf-8")
    code = main(["--project", str(proj)], stages=STUBS)
    err = capsys.readouterr().err
    assert code == 1
    assert "recipe.json" in err
    assert "settings.min_obs_per_cell:" in err
    assert "Traceback" not in err and "pydantic" not in err and "input_value" not in err

    data["settings"] = {"min_obs_per_cell": 3}
    (proj / "recipe.json").write_text(json.dumps(data), encoding="utf-8")
    assert main(["--project", str(proj)], stages=STUBS) == 1
    assert "stages.alpha.factor:" in capsys.readouterr().err


class _EmptyParams(BaseModel):
    pass


def _raising_stage(label: str, exc: Exception) -> type[Any]:
    class _Raising:
        name: ClassVar[str] = label
        version: ClassVar[str] = "1"
        depends_on: ClassVar[tuple[str, ...]] = ()
        config_files: ClassVar[tuple[str, ...]] = ()
        Params: ClassVar[type[Any]] = _EmptyParams

        def run(self, inputs: Any, params: Any, vocab: Any) -> StageResult[Any]:
            raise exc

    return _Raising


def test_blocking_item_exit_1_prints_item_and_commits_nothing(
    proj: Path, defaults: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--project", str(proj)], stages=(stub_stages.Eta,)) == 1
    err = capsys.readouterr().err
    assert "Blocked: eta: stub blocking problem" in err
    assert "Internal error" not in err
    assert _checkpoints(proj, "eta") == []
    assert not (proj / "cache" / "heads.json").exists()
    assert not (proj / "log" / "decisions.jsonl").exists()


def test_unresolved_synced_gates_still_exit_2_with_suggestions_listed(
    proj: Path, defaults: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stages = (stub_stages.Keys, stub_stages.Zeta)
    assert main(["--project", str(proj)], stages=stages) == 2
    out = capsys.readouterr().out
    assert "zeta.entries[a].decision (suggested: 's_a:1')" in out
    assert "zeta.entries[b].decision (suggested: 's_b:1')" in out
    assert _checkpoints(proj, "zeta") == []


def test_accept_defaults_resolves_synced_suggested_gates(
    proj: Path, defaults: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stages = (stub_stages.Keys, stub_stages.Zeta)
    assert main(["--project", str(proj), "--accept-defaults"], stages=stages) == 0
    recipe = json.loads((proj / "recipe.json").read_text(encoding="utf-8"))
    assert recipe["stages"]["zeta"]["entries"]["a"]["decision"] == {
        "accept_default": True,
        "value": "s_a:1",
    }
    assert len(_checkpoints(proj, "zeta")) == 1


def test_unexpected_stage_error_is_logged_not_printed(
    proj: Path, defaults: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stages = (Alpha, _raising_stage("boom", KeyError("missing_column")))
    assert main(["--project", str(proj)], stages=stages) == 1
    err = capsys.readouterr().err
    logfile = proj / "log" / "errors.log"
    assert "Internal error in stage boom: KeyError: 'missing_column'" in err
    assert f"details in {logfile}" in err
    assert "Traceback" not in err
    text = logfile.read_text(encoding="utf-8")
    assert "Traceback" in text and "missing_column" in text and "stage=boom" in text
    assert main(["--project", str(proj)], stages=stages) == 1
    assert logfile.read_text(encoding="utf-8").count("=== ") == 2


@pytest.mark.parametrize(
    "exc",
    [
        FileLockedError(
            "Cannot read 'lab_a.xlsx'. If it is open in Excel, close it and try again."
        ),
        DependencyNotCommittedError("beta", "alpha", "stale"),
        ConfigError("tokens.json: key 'decimal_separator' must be one of '.' or ','."),
    ],
    ids=["locked-file", "dependency", "config"],
)
def test_plain_language_errors_in_a_stage_are_not_internal_errors(
    exc: Exception, proj: Path, defaults: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--project", str(proj)], stages=(_raising_stage("plain", exc),)) == 1
    err = capsys.readouterr().err
    assert str(exc) in err
    assert "Internal error" not in err
    assert not (proj / "log" / "errors.log").exists()
