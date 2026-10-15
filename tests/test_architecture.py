"""Architecture tests: core has no Dash; stages honour StageResult and immutability contracts."""

from __future__ import annotations

import ast
import copy
import dataclasses
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel

from core.config import CONFIG_FILES
from core.pipeline import ReportItem, StageResult
from core.stages import STAGES
from core.vocab import Vocabulary
from tests.arch_inputs import ARCH_INPUTS
from tests.stub_stages import STUB_ARCH_INPUTS, STUBS_WITH_GAMMA

# Real stages plus the stub stages, so the checks execute before any real stage exists.
ALL_STAGES: tuple[type[Any], ...] = (*STAGES, *STUBS_WITH_GAMMA)
ALL_INPUTS: dict[str, Callable[[], tuple[Any, Any]]] = {**STUB_ARCH_INPUTS, **ARCH_INPUTS}

CORE_DIR = Path(__file__).resolve().parents[1] / "core"


# ---- detectors -------------------------------------------------------------------------


def find_dash_imports(source: str) -> list[str]:
    """Return imported module names whose root is `dash` or `dash_*`."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for name in names:
            root = name.split(".")[0]
            if root == "dash" or root.startswith("dash_"):
                found.append(name)
    return found


def find_app_imports(source: str) -> list[str]:
    """Return imported module names whose root is `app` (core must never import the UI)."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        found.extend(n for n in names if n.split(".")[0] == "app")
    return found


def scan_dash_imports(root: Path) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        names = find_dash_imports(path.read_text(encoding="utf-8"))
        if names:
            hits[str(path.relative_to(root))] = names
    return hits


def scan_app_imports(root: Path) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        names = find_app_imports(path.read_text(encoding="utf-8"))
        if names:
            hits[str(path.relative_to(root))] = names
    return hits


_STAGE_ATTRS = ("name", "version", "depends_on", "config_files", "Params")


def stage_contract_problems(
    stages: Sequence[type[Any]], known_config: Iterable[str] = CONFIG_FILES
) -> list[str]:
    """Plain-language problems with the class-level contract of a set of stages."""
    problems: list[str] = []
    known = set(known_config)
    names = [getattr(s, "name", s.__name__) for s in stages]
    for dup in sorted({n for n in names if names.count(n) > 1}):
        problems.append(f"stage name {dup!r} is registered more than once")
    for s in stages:
        label = getattr(s, "name", s.__name__)
        for attr in _STAGE_ATTRS:
            if not hasattr(s, attr):
                problems.append(f"{label}: missing class attribute {attr!r}")
        if not hasattr(s, "run"):
            problems.append(f"{label}: missing run()")
        params = getattr(s, "Params", None)
        if params is not None and not (isinstance(params, type) and issubclass(params, BaseModel)):
            problems.append(f"{label}: Params must be a pydantic BaseModel subclass")
        for f in getattr(s, "config_files", ()):
            if f not in known:
                problems.append(f"{label}: config file {f!r} is not a known config file")
        for d in getattr(s, "depends_on", ()):
            if d not in names:
                problems.append(f"{label}: depends on unregistered stage {d!r}")
    return problems


def missing_arch_inputs(
    stages: Iterable[type[Any]], inputs_map: Mapping[str, Callable[[], tuple[Any, Any]]]
) -> list[str]:
    return sorted(s.name for s in stages if s.name not in inputs_map)


def check_stage_result(
    stage: type[Any], inputs: Any, params: Any, vocab: Vocabulary | None = None
) -> StageResult[Any]:
    result = stage().run(inputs, params, vocab)
    assert isinstance(result, StageResult), f"{stage.name}: run() must return StageResult"
    assert isinstance(result.report, tuple), f"{stage.name}: report must be a tuple"
    assert len(result.report) >= 1, f"{stage.name}: report must not be empty"
    bad = [type(i).__name__ for i in result.report if not isinstance(i, ReportItem)]
    assert not bad, f"{stage.name}: report holds non-ReportItem entries {bad}"
    return result


def assert_equal_deep(a: Any, b: Any, path: str = "root") -> None:
    if isinstance(a, pd.DataFrame) and isinstance(b, pd.DataFrame):
        pd.testing.assert_frame_equal(a, b, obj=path)
    elif isinstance(a, pd.Series) and isinstance(b, pd.Series):
        pd.testing.assert_series_equal(a, b, obj=path)
    elif isinstance(a, np.ndarray) and isinstance(b, np.ndarray):
        np.testing.assert_array_equal(a, b, err_msg=path)
    elif dataclasses.is_dataclass(a) and not isinstance(a, type):
        assert type(a) is type(b), f"{path}: type {type(a)} != {type(b)}"
        for f in dataclasses.fields(a):
            assert_equal_deep(getattr(a, f.name), getattr(b, f.name), f"{path}.{f.name}")
    elif isinstance(a, Mapping) and isinstance(b, Mapping):
        assert list(a.keys()) == list(b.keys()), f"{path}: keys differ"
        for k in a:
            assert_equal_deep(a[k], b[k], f"{path}[{k!r}]")
    elif isinstance(a, Sequence) and not isinstance(a, str | bytes):
        assert isinstance(b, Sequence) and len(a) == len(b), f"{path}: length differs"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            assert_equal_deep(x, y, f"{path}[{i}]")
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


def check_no_mutation(
    stage: type[Any], inputs: Any, params: Any, vocab: Vocabulary | None = None
) -> None:
    inputs_before = copy.deepcopy(inputs)
    params_before = copy.deepcopy(params)
    stage().run(inputs, params, vocab)
    assert_equal_deep(inputs, inputs_before, "inputs")
    assert_equal_deep(params, params_before, "params")


# ---- fakes for detector self-tests -------------------------------------------------------


@dataclasses.dataclass
class Holder:
    df: pd.DataFrame
    tag: str = "x"


def _item() -> ReportItem:
    return ReportItem(action="noop", target="t", count=0, params={})


class GoodStage:
    name: ClassVar[str] = "good"
    version: ClassVar[str] = "1"
    depends_on: ClassVar[tuple[str, ...]] = ()
    config_files: ClassVar[tuple[str, ...]] = ()
    Params: ClassVar[type[Any]] = BaseModel

    def run(self, inputs: Holder, params: None, vocab: None) -> StageResult[pd.DataFrame]:
        return StageResult(output=inputs.df.assign(b=1), report=(_item(),))


class BareDataFrameStage(GoodStage):
    name: ClassVar[str] = "bare"

    def run(self, inputs: Holder, params: None, vocab: None) -> Any:
        return inputs.df.copy()


class ListReportStage(GoodStage):
    name: ClassVar[str] = "list_report"

    def run(self, inputs: Holder, params: None, vocab: None) -> Any:
        return StageResult(output=inputs.df, report=[_item()])  # type: ignore[arg-type]


class EmptyReportStage(GoodStage):
    name: ClassVar[str] = "empty_report"

    def run(self, inputs: Holder, params: None, vocab: None) -> StageResult[pd.DataFrame]:
        return StageResult(output=inputs.df, report=())


class MutatingStage(GoodStage):
    name: ClassVar[str] = "mutating"

    def run(self, inputs: Holder, params: None, vocab: None) -> StageResult[pd.DataFrame]:
        inputs.df.loc[0, "a"] = 99
        return StageResult(output=inputs.df.copy(), report=(_item(),))


def _holder() -> Holder:
    return Holder(df=pd.DataFrame({"a": [1, 2, 3]}))


# ---- real-code tests ---------------------------------------------------------------------


def test_core_has_no_dash_imports() -> None:
    assert scan_dash_imports(CORE_DIR) == {}


def test_core_does_not_import_app() -> None:
    assert scan_app_imports(CORE_DIR) == {}


def test_stage_contract_attributes() -> None:
    assert stage_contract_problems(ALL_STAGES) == []


def test_every_stage_has_arch_inputs() -> None:
    assert missing_arch_inputs(ALL_STAGES, ALL_INPUTS) == []


@pytest.mark.parametrize("stage", ALL_STAGES, ids=lambda s: s.name)
def test_registered_stage_returns_stage_result(stage: type[Any], arch_vocab: Vocabulary) -> None:
    inputs, params = ALL_INPUTS[stage.name]()
    check_stage_result(stage, inputs, params, arch_vocab)


@pytest.mark.parametrize("stage", ALL_STAGES, ids=lambda s: s.name)
def test_registered_stage_does_not_mutate_inputs(stage: type[Any], arch_vocab: Vocabulary) -> None:
    inputs, params = ALL_INPUTS[stage.name]()
    check_no_mutation(stage, inputs, params, arch_vocab)


# ---- detector self-tests -----------------------------------------------------------------


def test_dash_detector_flags_bad_sources() -> None:
    assert find_dash_imports("import dash") == ["dash"]
    assert find_dash_imports("from dash import html") == ["dash"]
    assert find_dash_imports("import dash_bootstrap_components as dbc") == [
        "dash_bootstrap_components"
    ]
    assert find_dash_imports("from dash.dependencies import Input") == ["dash.dependencies"]


def test_dash_detector_passes_good_sources() -> None:
    source = "import pandas as pd\nfrom core import pipeline\nimport dashboard"
    assert find_dash_imports(source) == []


def test_dash_scan_finds_file_in_fake_core(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("import pandas\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "bad.py").write_text("from dash import html\n", encoding="utf-8")
    assert scan_dash_imports(tmp_path) == {str(Path("sub") / "bad.py"): ["dash"]}


def test_app_detector_flags_bad_sources() -> None:
    assert find_app_imports("import app") == ["app"]
    assert find_app_imports("from app.layout import x") == ["app.layout"]
    assert find_app_imports("import application\nfrom core import app_state") == []


def test_app_scan_finds_file_in_fake_core(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("import pandas\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("from app import ui\n", encoding="utf-8")
    assert scan_app_imports(tmp_path) == {"bad.py": ["app"]}


class _NoParams(GoodStage):
    name: ClassVar[str] = "no_params"
    Params: ClassVar[type[Any]] = dict


class _UnknownConfig(GoodStage):
    name: ClassVar[str] = "unknown_config"
    config_files: ClassVar[tuple[str, ...]] = ("not_a_config.csv",)


class _OrphanDep(GoodStage):
    name: ClassVar[str] = "orphan_dep"
    depends_on: ClassVar[tuple[str, ...]] = ("ghost",)


class _MissingAttrs:
    name: ClassVar[str] = "missing_attrs"


def test_contract_check_detects_bad_stages() -> None:
    assert any("Params" in p for p in stage_contract_problems([_NoParams]))
    assert any("not_a_config.csv" in p for p in stage_contract_problems([_UnknownConfig]))
    assert any("ghost" in p for p in stage_contract_problems([_OrphanDep]))
    missing = stage_contract_problems([_MissingAttrs])
    assert any("version" in p for p in missing) and any("run()" in p for p in missing)
    assert any("more than once" in p for p in stage_contract_problems([GoodStage, GoodStage]))


def test_missing_arch_inputs_detector() -> None:
    assert missing_arch_inputs([GoodStage], {}) == ["good"]
    assert missing_arch_inputs([GoodStage], {"good": lambda: (None, None)}) == []


def test_stage_result_check_accepts_good_stage() -> None:
    check_stage_result(GoodStage, _holder(), None)


@pytest.mark.parametrize("bad", [BareDataFrameStage, ListReportStage, EmptyReportStage])
def test_stage_result_check_rejects_bad_stages(bad: type[Any]) -> None:
    with pytest.raises(AssertionError):
        check_stage_result(bad, _holder(), None)


def test_mutation_check_accepts_pure_stage() -> None:
    check_no_mutation(GoodStage, _holder(), None)


def test_mutation_check_rejects_mutating_stage_with_nested_dataframe() -> None:
    with pytest.raises(AssertionError):
        check_no_mutation(MutatingStage, _holder(), None)


def test_report_item_params_are_read_only_and_evidence_ignored_in_equality() -> None:
    a = ReportItem("a", "t", 1, {"k": 1}, evidence=pd.DataFrame({"x": [1]}))
    b = ReportItem("a", "t", 1, {"k": 1}, evidence=None)
    assert a == b
    with pytest.raises(TypeError):
        a.params["k"] = 2  # type: ignore[index]
