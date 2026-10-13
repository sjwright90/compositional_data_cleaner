"""Architecture tests: core has no Dash; stages honour StageResult and immutability contracts."""

from __future__ import annotations

import ast
import copy
import dataclasses
import typing
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pandas as pd
import pytest

from core.pipeline import ReportItem, Stage, StageResult
from core.stages import STAGES
from tests.arch_inputs import ARCH_INPUTS

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


def scan_dash_imports(root: Path) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        names = find_dash_imports(path.read_text(encoding="utf-8"))
        if names:
            hits[str(path.relative_to(root))] = names
    return hits


def missing_arch_inputs(
    stages: Iterable[type[Any]], inputs_map: Mapping[str, Callable[[], tuple[Any, Any]]]
) -> list[str]:
    return sorted(s.name for s in stages if s.name not in inputs_map)


def check_stage_result(stage: type[Any], inputs: Any, params: Any) -> StageResult[Any]:
    result = stage().run(inputs, params, None)
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


def check_no_mutation(stage: type[Any], inputs: Any, params: Any) -> None:
    inputs_before = copy.deepcopy(inputs)
    params_before = copy.deepcopy(params)
    stage().run(inputs, params, None)
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


def test_stage_declares_config_files_classvar() -> None:
    hints = typing.get_type_hints(Stage)
    assert hints["config_files"] == ClassVar[tuple[str, ...]]


def test_every_stage_has_arch_inputs() -> None:
    assert missing_arch_inputs(STAGES, ARCH_INPUTS) == []


@pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.name)
def test_registered_stage_returns_stage_result(stage: type[Any]) -> None:
    inputs, params = ARCH_INPUTS[stage.name]()
    check_stage_result(stage, inputs, params)


@pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.name)
def test_registered_stage_does_not_mutate_inputs(stage: type[Any]) -> None:
    inputs, params = ARCH_INPUTS[stage.name]()
    check_no_mutation(stage, inputs, params)


def test_layer_helpers_are_not_stages() -> None:
    """A4: impute layers are plain helpers; only classes listed in STAGES are checked."""

    def layer(df: pd.DataFrame) -> tuple[pd.DataFrame, tuple[ReportItem, ...]]:
        return df, ()

    assert all(layer is not s and getattr(s, "run", None) is not layer for s in STAGES)


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
