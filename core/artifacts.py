"""Immutable artifact containers that enforce their own invariants (spec §4.1, §5.1, §5.2)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

import numpy as np
import pandas as pd

LOCATION_LEVEL = "location_id"


class CellStatus(StrEnum):
    DETECT = "detect"
    ND = "nd"
    GT = "gt"  # only if Q11 option "keep bound" is chosen
    MISSING_BAD = "missing_bad"
    MISSING_ZERO = "missing_zero"
    MISSING_NO_RESULT = "missing_no_result"
    MISSING_ND_NO_DL = "missing_nd_no_dl"
    MISSING_RIGHT_CENSORED = "missing_right_censored"
    MISSING_UNMEASURED = "missing_unmeasured"  # no row existed for this sample x feature


ALL_STATUSES: tuple[str, ...] = tuple(s.value for s in CellStatus)
MISSING_STATUSES: frozenset[str] = frozenset(
    s.value for s in CellStatus if s.name.startswith("MISSING_")
)
# A5: only DETECT and ND count toward min_obs_per_cell; GT is "observed" for display only.
COUNTED_STATUSES: frozenset[str] = frozenset({CellStatus.DETECT.value, CellStatus.ND.value})
_IMPUTED_STATUSES: frozenset[str] = MISSING_STATUSES | {CellStatus.ND.value}


def _as_float_frame(df: pd.DataFrame, name: str) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"{name} must be a DataFrame, got {type(df).__name__}")
    try:
        return df.astype(float)  # astype returns a new frame
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric: {exc}") from exc


def _status_frame(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"status must be a DataFrame, got {type(df).__name__}")
    as_obj = df.astype(object)
    bad = ~as_obj.isin(ALL_STATUSES) & as_obj.notna()
    if bool(bad.any().any()) or bool(as_obj.isna().any().any()):
        raise ValueError("status contains values that are not a CellStatus")
    dtype = pd.CategoricalDtype(categories=list(ALL_STATUSES))
    return as_obj.astype(dtype)


def _require_aligned(**frames: pd.DataFrame) -> None:
    names = list(frames)
    first = frames[names[0]]
    for other in names[1:]:
        if not first.index.equals(frames[other].index):
            raise ValueError(f"index of {other} does not match {names[0]}")
        if not first.columns.equals(frames[other].columns):
            raise ValueError(f"columns of {other} do not match {names[0]}")


def _location_level(index: pd.Index) -> pd.Index:
    if LOCATION_LEVEL in index.names:
        return index.get_level_values(LOCATION_LEVEL)
    return index.get_level_values(0)


def _mask(status: pd.DataFrame, members: Iterable[str]) -> pd.DataFrame:
    return status.astype(object).isin(list(members))


@dataclass(frozen=True, eq=False)
class WideTable:
    values: pd.DataFrame  # index = sample key (MultiIndex), columns = FeatureId.canonical()
    status: pd.DataFrame  # same shape; CellStatus categorical
    dl: pd.DataFrame  # same shape; float, NaN unless status == ND

    def __post_init__(self) -> None:
        values = _as_float_frame(self.values, "values")
        dl = _as_float_frame(self.dl, "dl")
        status = _status_frame(self.status)
        _require_aligned(values=values, status=status, dl=dl)

        nd = _mask(status, [CellStatus.ND.value])
        missing = _mask(status, MISSING_STATUSES)
        if bool((nd & dl.isna()).any().any()):
            raise ValueError("ND cell without a dl")
        if bool((~nd & dl.notna()).any().any()):
            raise ValueError("dl set on a cell whose status is not ND")
        if bool((nd & (values != dl)).any().any()):
            raise ValueError("ND cell value must equal its dl")
        if bool((missing & values.notna()).any().any()):
            raise ValueError("MISSING_* cell has a value")
        if bool((~missing & values.isna()).any().any()):
            raise ValueError("non-missing cell (DETECT/ND/GT) has a NaN value")

        object.__setattr__(self, "values", values)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "dl", dl)

    @property
    def nd_mask(self) -> pd.DataFrame:
        return _mask(self.status, [CellStatus.ND.value])

    @property
    def missing_mask(self) -> pd.DataFrame:
        return _mask(self.status, MISSING_STATUSES)

    def select(self, locations: Sequence[str], features: Sequence[str]) -> WideTable:
        loc_level = _location_level(self.values.index)
        unknown_loc = sorted(set(locations) - set(loc_level))
        if unknown_loc:
            raise KeyError(f"unknown locations: {unknown_loc}")
        unknown_feat = [f for f in features if f not in self.values.columns]
        if unknown_feat:
            raise KeyError(f"unknown features: {unknown_feat}")
        rows = loc_level.isin(list(locations))
        cols = list(features)
        return WideTable(
            self.values.loc[rows, cols],
            self.status.loc[rows, cols],
            self.dl.loc[rows, cols],
        )

    def to_export(self, names: Mapping[str, str]) -> pd.DataFrame:
        missing_names = [c for c in self.values.columns if c not in names]
        if missing_names:
            raise KeyError(f"no output name for features: {missing_names}")
        out_names = [names[c] for c in self.values.columns]
        if len(set(out_names)) != len(out_names):
            raise ValueError("output names are not unique")
        out = self.values.astype(object)
        nd = self.nd_mask
        for col in out.columns:
            hit = nd[col].to_numpy()
            if hit.any():
                out.loc[hit, col] = [
                    "<" + np.format_float_positional(d, trim="-") for d in self.dl.loc[hit, col]
                ]
        out.columns = pd.Index(out_names)
        return out

    def observed_count(self, axis: Literal["samples", "features"]) -> pd.Series:
        return observed_count(self, axis=axis)


def observed_count(table: WideTable, axis: Literal["samples", "features"]) -> pd.Series:
    """Cells counting toward `min_obs_per_cell`: DETECT and ND only (A5)."""
    counted = _mask(table.status, COUNTED_STATUSES)
    if axis == "samples":
        return counted.sum(axis=1).astype(int)
    if axis == "features":
        return counted.sum(axis=0).astype(int)
    raise ValueError(f"axis must be 'samples' or 'features', got {axis!r}")


@dataclass(frozen=True, eq=False)
class ImputerInput:
    """Frozen "final suite" entering the imputer (§6.8)."""

    table: WideTable
    log_features: frozenset[str]
    allow_negative: frozenset[str]
    params: Any  # ImputeParams, defined in T40
    fingerprint: str

    def __post_init__(self) -> None:
        cols = set(self.table.values.columns)
        for name in ("log_features", "allow_negative"):
            feats = frozenset(getattr(self, name))
            if not feats <= cols:
                raise ValueError(f"{name} has features not in the table: {sorted(feats - cols)}")
            object.__setattr__(self, name, feats)


@dataclass(frozen=True, eq=False)
class ImputedTable:
    values: pd.DataFrame
    input_status: pd.DataFrame  # status as it entered the imputer

    def __post_init__(self) -> None:
        values = _as_float_frame(self.values, "values")
        status = _status_frame(self.input_status)
        _require_aligned(values=values, input_status=status)
        if bool(values.isna().any().any()):
            raise ValueError("imputed values contain NaN")
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "input_status", status)

    @property
    def imputed_mask(self) -> pd.DataFrame:
        return _mask(self.input_status, _IMPUTED_STATUSES)


@dataclass(frozen=True, eq=False)
class _FrameArtifact:
    """DataFrame wrapper validated by its `core.contracts` schema; the input is copied."""

    frame: pd.DataFrame

    def _validate(self, frame: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    def __post_init__(self) -> None:
        object.__setattr__(self, "frame", self._validate(self.frame.copy(deep=True)))


class RawLong(_FrameArtifact):
    def _validate(self, frame: pd.DataFrame) -> pd.DataFrame:
        from core.contracts import validate_raw_long

        return validate_raw_long(frame)


class CleanLong(_FrameArtifact):
    def _validate(self, frame: pd.DataFrame) -> pd.DataFrame:
        from core.contracts import validate_clean_long

        return validate_clean_long(frame)


class Metadata(_FrameArtifact):
    def _validate(self, frame: pd.DataFrame) -> pd.DataFrame:
        from core.contracts import validate_metadata

        return validate_metadata(frame)
