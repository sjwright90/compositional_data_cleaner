"""Pipeline contract types: report items, stage results and the Stage protocol."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Protocol, TypeVar

import pandas as pd

if TYPE_CHECKING:
    Vocabulary = Any  # replaced by core.vocab.Vocabulary in T04

In = TypeVar("In", contravariant=True)
P = TypeVar("P", contravariant=True)
Out = TypeVar("Out", covariant=True)


@dataclass(frozen=True)
class ReportItem:
    action: str
    target: str
    count: int
    params: Mapping[str, Any] = field(default_factory=dict)
    evidence: pd.DataFrame | None = field(default=None, compare=False, hash=False)
    warning: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


@dataclass(frozen=True)
class StageResult(Generic[Out]):
    output: Out
    report: tuple[ReportItem, ...]


class Stage(Protocol[In, P, Out]):
    name: ClassVar[str]
    version: ClassVar[str]
    depends_on: ClassVar[tuple[str, ...]]
    config_files: ClassVar[tuple[str, ...]]
    Params: ClassVar[type[Any]]

    def run(self, inputs: In, params: P, vocab: Vocabulary) -> StageResult[Out]: ...
