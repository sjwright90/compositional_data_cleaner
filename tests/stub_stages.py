"""Two trivial stages for orchestrator and runner tests (not registered in core.stages)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

import pandas as pd
from pydantic import BaseModel, ConfigDict

from core.pipeline import ReportItem, StageResult
from core.recipe import Gate, gate_field
from core.vocab import Vocabulary

CALLS: list[str] = []  # stage names in call order; tests clear it


class AlphaParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    factor: int = 1


class BetaParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Gate[str] = gate_field("keep")
    tag: str = "t"


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


STUBS: tuple[type[Any], ...] = (Alpha, Beta)
