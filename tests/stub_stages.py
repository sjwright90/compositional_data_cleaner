"""Two trivial stages for orchestrator and runner tests (not registered in core.stages)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

import pandas as pd
from pydantic import BaseModel, ConfigDict

from core.pipeline import ReportItem, StageResult, SyncResult
from core.recipe import AcceptDefault, Gate, gate_field
from core.vocab import Vocabulary

CALLS: list[str] = []  # stage names in call order; tests clear it


class AlphaParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    factor: int = 1


class BetaParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Gate[str] = gate_field("keep")
    tag: str = "t"


class GammaParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scale: int = 1


@dataclass(frozen=True)
class BetaOut:
    table: pd.DataFrame
    label: str


class Alpha:
    name: ClassVar[str] = "alpha"
    version: ClassVar[str] = "1"
    depends_on: ClassVar[tuple[str, ...]] = ()
    config_files: ClassVar[tuple[str, ...]] = ("tokens.json",)
    Params: ClassVar[type[Any]] = AlphaParams

    def run(
        self, inputs: tuple[Any, ...], params: AlphaParams, vocab: Vocabulary
    ) -> StageResult[pd.DataFrame]:
        CALLS.append(self.name)
        out = pd.DataFrame({"n_sources": [len(inputs)], "x": [params.factor]})
        report = (
            ReportItem(
                "scale",
                "x",
                1,
                {"factor": params.factor},
                evidence=pd.DataFrame({"row": [0], "x": [params.factor]}),
            ),
            ReportItem("note", "sources", len(inputs), {}, warning="stub warning"),
        )
        return StageResult(out, report)


class Beta:
    name: ClassVar[str] = "beta"
    version: ClassVar[str] = "1"
    depends_on: ClassVar[tuple[str, ...]] = ("alpha",)
    config_files: ClassVar[tuple[str, ...]] = ()
    Params: ClassVar[type[Any]] = BetaParams

    def run(
        self, inputs: pd.DataFrame, params: BetaParams, vocab: Vocabulary
    ) -> StageResult[BetaOut]:
        CALLS.append(self.name)
        table = inputs.assign(tag=params.tag)
        report = (ReportItem("tag", "tag", len(table), {"tag": params.tag}),)
        return StageResult(BetaOut(table, "beta"), report)


class Gamma:
    """Independent root stage reading a different config file than Alpha."""

    name: ClassVar[str] = "gamma"
    version: ClassVar[str] = "1"
    depends_on: ClassVar[tuple[str, ...]] = ()
    config_files: ClassVar[tuple[str, ...]] = ("known_units.csv",)
    Params: ClassVar[type[Any]] = GammaParams

    def run(
        self, inputs: tuple[Any, ...], params: GammaParams, vocab: Vocabulary
    ) -> StageResult[pd.DataFrame]:
        CALLS.append(self.name)
        out = pd.DataFrame({"scale": [params.scale]})
        return StageResult(out, (ReportItem("scale", "scale", 1, {"scale": params.scale}),))


# ---- T11a plumbing stubs: gates in collections, suggest, sync_params, blocking, vocab usage ----


class SheetDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    layout: Gate[str] = gate_field("wide")


class MapEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    unit: Gate[str] = gate_field("mg/L")


class DeltaParams(BaseModel):
    """Gates inside a list and a dict of sub-models."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sheets: list[SheetDecision] = []
    maps: dict[str, MapEntry] = {}


class KeysParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    keys: list[str] = ["a", "b"]
    n: int = 1


class Entry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Gate[str] = gate_field("keep", suggested=True)


class ZetaParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: dict[str, Entry] = {}


class EtaParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    block: bool = True


class Delta:
    name: ClassVar[str] = "delta"
    version: ClassVar[str] = "1"
    depends_on: ClassVar[tuple[str, ...]] = ()
    config_files: ClassVar[tuple[str, ...]] = ()
    Params: ClassVar[type[Any]] = DeltaParams

    def run(
        self, inputs: tuple[Any, ...], params: DeltaParams, vocab: Vocabulary
    ) -> StageResult[pd.DataFrame]:
        CALLS.append(self.name)
        out = pd.DataFrame({"n_sheets": [len(params.sheets)], "n_maps": [len(params.maps)]})
        return StageResult(out, (ReportItem("count", "sheets", len(params.sheets)),))


class Keys:
    """Root stage whose output (keys, n) drives `Zeta.sync_params` and `Zeta.suggest`."""

    name: ClassVar[str] = "keys"
    version: ClassVar[str] = "1"
    depends_on: ClassVar[tuple[str, ...]] = ()
    config_files: ClassVar[tuple[str, ...]] = ()
    Params: ClassVar[type[Any]] = KeysParams

    def run(
        self, inputs: tuple[Any, ...], params: KeysParams, vocab: Vocabulary
    ) -> StageResult[pd.DataFrame]:
        CALLS.append(self.name)
        out = pd.DataFrame({"key": list(params.keys), "n": params.n})
        return StageResult(out, (ReportItem("keys", "keys", len(out)),))


def _resolved(value: Any) -> Any:
    return value.value if isinstance(value, AcceptDefault) else value


class Zeta:
    name: ClassVar[str] = "zeta"
    version: ClassVar[str] = "1"
    depends_on: ClassVar[tuple[str, ...]] = ("keys",)
    config_files: ClassVar[tuple[str, ...]] = ()
    Params: ClassVar[type[Any]] = ZetaParams

    def run(
        self, inputs: pd.DataFrame, params: ZetaParams, vocab: Vocabulary
    ) -> StageResult[pd.DataFrame]:
        CALLS.append(self.name)
        keys = [k for k in inputs["key"] if k in params.entries]  # orphans are ignored
        out = pd.DataFrame(
            {"key": keys, "decision": [_resolved(params.entries[k].decision) for k in keys]}
        )
        return StageResult(out, (ReportItem("decide", "entries", len(out)),))

    def sync_params(
        self, inputs: pd.DataFrame, params: ZetaParams, vocab: Vocabulary
    ) -> SyncResult:
        keys = {str(k) for k in inputs["key"]}
        entries = dict(params.entries)
        for key in sorted(keys - set(entries)):
            entries[key] = Entry()
        orphans: list[str] = []
        for key in sorted(set(entries) - keys):
            if entries[key].decision is None:
                del entries[key]  # nothing decided: nothing worth keeping
            else:
                orphans.append(f"entries[{key}]")
        return SyncResult(params.model_copy(update={"entries": entries}), tuple(orphans))

    def suggest(
        self, inputs: pd.DataFrame, params: ZetaParams, vocab: Vocabulary
    ) -> dict[str, Any]:
        n = int(inputs["n"].iloc[0]) if len(inputs) else 0
        return {
            f"entries[{k}].decision": f"s_{k}:{n}"
            for k in sorted(params.entries)
            if not k.startswith("n")  # keys starting with "n" get no suggestion
        }


class Eta:
    """Root stage that looks up vocabulary entries and can emit a blocking item."""

    name: ClassVar[str] = "eta"
    version: ClassVar[str] = "1"
    depends_on: ClassVar[tuple[str, ...]] = ()
    config_files: ClassVar[tuple[str, ...]] = ()
    Params: ClassVar[type[Any]] = EtaParams

    def run(
        self, inputs: tuple[Any, ...], params: EtaParams, vocab: Vocabulary
    ) -> StageResult[pd.DataFrame]:
        CALLS.append(self.name)
        vocab.analyte("mn")
        vocab.unit("mg/L")
        vocab.threshold("min_obs_per_cell")
        vocab.token("no_result_tokens")
        report = [ReportItem("looked_up", "vocab", 4)]
        if params.block:
            report.append(
                ReportItem("bad_rows", "rows", 2, warning="stub blocking problem", blocking=True)
            )
        return StageResult(pd.DataFrame({"x": [1]}), tuple(report))


STUBS: tuple[type[Any], ...] = (Alpha, Beta)
STUBS_WITH_GAMMA: tuple[type[Any], ...] = (Alpha, Beta, Gamma)
STUBS_PLUMBING: tuple[type[Any], ...] = (Delta, Keys, Zeta, Eta)

# stage name -> (inputs, params) builder, for the architecture checks
STUB_ARCH_INPUTS: dict[str, Callable[[], tuple[Any, Any]]] = {
    "alpha": lambda: ((), AlphaParams()),
    "beta": lambda: (pd.DataFrame({"n_sources": [0], "x": [1]}), BetaParams(mode="keep")),
    "gamma": lambda: ((), GammaParams()),
    "delta": lambda: (
        (),
        DeltaParams(
            sheets=[SheetDecision(layout="wide"), SheetDecision()],
            maps={"mn": MapEntry(unit="mg/L")},
        ),
    ),
    "keys": lambda: ((), KeysParams()),
    "zeta": lambda: (
        pd.DataFrame({"key": ["a", "b"], "n": [1, 1]}),
        ZetaParams(entries={"a": Entry(decision="keep"), "old": Entry(decision="drop")}),
    ),
    "eta": lambda: ((), EtaParams()),
}
