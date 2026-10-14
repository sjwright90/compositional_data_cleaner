"""Artifact container and contract tests (spec §4.1, §5.1, §9; addendum A5)."""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pandera.errors as pa_errors
import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.artifacts import (
    CellStatus,
    CleanLong,
    ImputedTable,
    ImputerInput,
    Metadata,
    RawLong,
    WideTable,
    observed_count,
)
from core.contracts import validate_raw_long, validate_wide

S = CellStatus
F_MN = "mn|D|mg/L|"
F_FE = "fe|T|mg/L|"


def _index(n: int) -> pd.MultiIndex:
    return pd.MultiIndex.from_tuples(
        [(f"L{i % 2}", f"2024-01-{i + 1:02d}") for i in range(n)],
        names=["location_id", "sample_date"],
    )


def _frames(
    rows: list[list[tuple[CellStatus, float, float]]],
    cols: tuple[str, ...] = (F_MN, F_FE),
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    idx = _index(len(rows))
    values = pd.DataFrame([[c[1] for c in r] for r in rows], index=idx, columns=list(cols))
    status = pd.DataFrame([[c[0].value for c in r] for r in rows], index=idx, columns=list(cols))
    dl = pd.DataFrame([[c[2] for c in r] for r in rows], index=idx, columns=list(cols))
    return values, status, dl


def _basic() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    nan = np.nan
    return _frames(
        [
            [(S.DETECT, 1.5, nan), (S.ND, 0.1, 0.1)],
            [(S.MISSING_BAD, nan, nan), (S.GT, 9.0, nan)],
            [(S.MISSING_UNMEASURED, nan, nan), (S.DETECT, 2.0, nan)],
        ]
    )


def _wide() -> WideTable:
    return WideTable(*_basic())


class TestWideTableInvariants:
    def test_valid_constructs(self) -> None:
        w = _wide()
        assert w.values.shape == (3, 2)
        assert list(w.status[F_MN]) == [S.DETECT, S.MISSING_BAD, S.MISSING_UNMEASURED]

    def test_misaligned_index_fails(self) -> None:
        v, s, d = _basic()
        with pytest.raises(ValueError, match="index"):
            WideTable(v, s.iloc[::-1], d)

    def test_misaligned_columns_fails(self) -> None:
        v, s, d = _basic()
        with pytest.raises(ValueError, match="columns"):
            WideTable(v, s.rename(columns={F_MN: "other|D|mg/L|"}), d)

    def test_nd_without_dl_fails(self) -> None:
        v, s, d = _basic()
        d.iloc[0, 1] = np.nan
        with pytest.raises(ValueError, match="ND"):
            WideTable(v, s, d)

    def test_nd_value_not_dl_fails(self) -> None:
        v, s, d = _basic()
        v.iloc[0, 1] = 0.2
        with pytest.raises(ValueError, match="ND"):
            WideTable(v, s, d)

    def test_missing_with_value_fails(self) -> None:
        v, s, d = _basic()
        v.iloc[1, 0] = 3.0
        with pytest.raises(ValueError, match="MISSING"):
            WideTable(v, s, d)

    def test_detect_with_nan_fails(self) -> None:
        v, s, d = _basic()
        v.iloc[0, 0] = np.nan
        with pytest.raises(ValueError, match="DETECT"):
            WideTable(v, s, d)

    def test_dl_on_non_nd_fails(self) -> None:
        v, s, d = _basic()
        d.iloc[0, 0] = 0.5
        with pytest.raises(ValueError, match="dl"):
            WideTable(v, s, d)

    def test_unknown_status_fails(self) -> None:
        v, s, d = _basic()
        s.iloc[0, 0] = "bogus"
        with pytest.raises(ValueError, match="status"):
            WideTable(v, s, d)


class TestImmutability:
    def test_masks_are_not_stored(self) -> None:
        w = _wide()
        names = {f.name for f in dataclasses.fields(w)}
        assert not {"nd_mask", "missing_mask"} & names
        assert "nd_mask" not in vars(w)
        assert isinstance(WideTable.nd_mask, property)

    def test_source_mutation_does_not_leak(self) -> None:
        v, s, d = _basic()
        w = WideTable(v, s, d)
        v.iloc[0, 0] = 999.0
        s.iloc[0, 0] = S.MISSING_BAD.value
        assert w.values.iloc[0, 0] == 1.5
        assert w.status.iloc[0, 0] == S.DETECT

    def test_frozen(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            _wide().values = pd.DataFrame()  # type: ignore[misc]

    def test_masks_derive_from_status(self) -> None:
        w = _wide()
        assert w.nd_mask.to_numpy().tolist() == [[False, True], [False, False], [False, False]]
        assert w.missing_mask.to_numpy().tolist() == [
            [False, False],
            [True, False],
            [True, False],
        ]


class TestSelectAndExport:
    def test_select_returns_new_object(self) -> None:
        w = _wide()
        sub = w.select(["L0"], [F_MN])
        assert sub is not w
        assert list(sub.values.columns) == [F_MN]
        assert set(sub.values.index.get_level_values("location_id")) == {"L0"}
        assert w.values.shape == (3, 2)

    def test_select_unknown_feature_raises(self) -> None:
        with pytest.raises(KeyError):
            _wide().select(["L0"], ["zn|D|mg/L|"])

    def test_select_unknown_location_raises(self) -> None:
        with pytest.raises(KeyError):
            _wide().select(["nope"], [F_MN])

    def test_to_export_renders_nd_and_names(self) -> None:
        out = _wide().to_export({F_MN: "Mn_D", F_FE: "Fe_T"})
        assert list(out.columns) == ["Mn_D", "Fe_T"]
        assert out.iloc[0]["Fe_T"] == "<0.1"
        assert out.iloc[0]["Mn_D"] == 1.5
        assert pd.isna(out.iloc[1]["Mn_D"])

    def test_to_export_missing_name_raises(self) -> None:
        with pytest.raises(KeyError):
            _wide().to_export({F_MN: "Mn_D"})


class TestObservedCount:
    def test_counts_detect_and_nd_only(self) -> None:
        w = _wide()
        per_feature = observed_count(w, axis="features")
        assert per_feature[F_MN] == 1  # DETECT only
        assert per_feature[F_FE] == 2  # ND + DETECT; GT excluded
        per_sample = observed_count(w, axis="samples")
        assert per_sample.tolist() == [2, 0, 1]

    def test_bad_axis(self) -> None:
        with pytest.raises(ValueError):
            observed_count(_wide(), axis="rows")  # type: ignore[arg-type]


class TestImputed:
    def test_imputed_mask_is_nd_union_missing(self) -> None:
        w = _wide()
        values = w.values.fillna(0.5)
        t = ImputedTable(values=values, input_status=w.status)
        assert t.imputed_mask.to_numpy().tolist() == [[False, True], [True, False], [True, False]]
        assert not t.imputed_mask.iloc[1, 1]  # GT not imputed

    def test_nan_values_fail(self) -> None:
        w = _wide()
        with pytest.raises(ValueError, match="NaN"):
            ImputedTable(values=w.values, input_status=w.status)

    def test_misaligned_fails(self) -> None:
        w = _wide()
        with pytest.raises(ValueError):
            ImputedTable(values=w.values.fillna(1.0).iloc[:2], input_status=w.status)


class TestImputerInput:
    def test_feature_subset_enforced(self) -> None:
        w = _wide()
        ok = ImputerInput(w, frozenset({F_MN}), frozenset(), params=None, fingerprint="x")
        assert ok.log_features == frozenset({F_MN})
        with pytest.raises(ValueError, match="features"):
            ImputerInput(w, frozenset({"zn|D|mg/L|"}), frozenset(), params=None, fingerprint="x")


class TestLongContracts:
    def _raw(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "location_id": ["L0"],
                "sample_date": ["2024-01-01"],
                "source_file": ["a.xlsx"],
                "source_sheet": ["s"],
                "source_row": [2],
                "analyte_raw": ["Mn"],
                "fraction_hint": ["D"],
                "unit_raw": ["mg/L"],
                "ingest_group_id": ["abc"],
                "raw_value": ["<0.1"],
                "value": [0.1],
                "qualifier": [""],
                "detect_flag_raw": [""],
                "dl_mdl_raw": [""],
                "dl_rl_raw": [""],
                "censor": ["<"],
            }
        )

    def _clean(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "location_id": ["L0"],
                "sample_date": [pd.Timestamp("2024-01-01")],
                "sample_type": ["normal"],
                "feature_id": [F_MN],
                "analyte": ["mn"],
                "fraction": ["D"],
                "variant": [""],
                "unit": ["mg/L"],
                "value": [0.1],
                "cell_status": ["nd"],
                "dl": [0.1],
                "dl_type": ["mdl"],
                "flag_action": [""],
            }
        )

    def test_raw_valid_and_wrapped(self) -> None:
        raw = RawLong(self._raw())
        assert len(raw.frame) == 1
        validate_raw_long(raw.frame)

    def test_raw_bad_censor_fails(self) -> None:
        df = self._raw()
        df["censor"] = ["~"]
        with pytest.raises(pa_errors.SchemaError):
            RawLong(df)

    def test_raw_missing_column_fails(self) -> None:
        with pytest.raises(pa_errors.SchemaError):
            validate_raw_long(self._raw().drop(columns=["ingest_group_id"]))

    def test_raw_extra_ingest_columns_pass(self) -> None:
        df = self._raw()
        df["method"] = ["ICP"]
        RawLong(df)

    def test_clean_valid(self) -> None:
        CleanLong(self._clean())

    def test_clean_bad_status_fails(self) -> None:
        df = self._clean()
        df["cell_status"] = ["bogus"]
        with pytest.raises(pa_errors.SchemaError):
            CleanLong(df)

    def test_wrapper_copies_source(self) -> None:
        df = self._clean()
        c = CleanLong(df)
        df.loc[0, "value"] = 7.0
        assert c.frame.loc[0, "value"] == 0.1

    def test_metadata_unique_location(self) -> None:
        Metadata(pd.DataFrame({"location_id": ["A", "B"], "x": [1, 2]}))
        with pytest.raises(pa_errors.SchemaError):
            Metadata(pd.DataFrame({"location_id": ["A", "A"], "x": [1, 2]}))

    def test_validate_wide(self) -> None:
        validate_wide(*_basic())
        v, s, d = _basic()
        s.iloc[0, 0] = "bogus"
        with pytest.raises(pa_errors.SchemaError):
            validate_wide(v, s, d)


_cell = st.one_of(
    st.tuples(st.just(S.DETECT), st.floats(0.01, 100), st.just(np.nan)),
    st.floats(0.01, 100).map(lambda x: (S.ND, x, x)),
    st.tuples(st.just(S.GT), st.floats(0.01, 100), st.just(np.nan)),
    st.sampled_from([s for s in S if s.name.startswith("MISSING_")]).map(
        lambda s: (s, np.nan, np.nan)
    ),
)


@given(st.lists(st.lists(_cell, min_size=2, max_size=2), min_size=1, max_size=6))
def test_consistent_frames_always_construct(
    rows: list[list[tuple[CellStatus, float, float]]],
) -> None:
    w = WideTable(*_frames(rows))
    n_nd = sum(c[0] is S.ND for r in rows for c in r)
    assert int(w.nd_mask.to_numpy().sum()) == n_nd
    assert int(observed_count(w, axis="samples").sum()) == sum(
        c[0] in (S.DETECT, S.ND) for r in rows for c in r
    )


@given(st.lists(st.lists(_cell, min_size=2, max_size=2), min_size=1, max_size=6), st.data())
def test_flipping_a_status_breaks_invariant(
    rows: list[list[tuple[CellStatus, float, float]]], data: st.DataObject
) -> None:
    v, s, d = _frames(rows)
    i = data.draw(st.integers(0, len(rows) - 1))
    j = data.draw(st.integers(0, 1))
    old = CellStatus(str(s.iloc[i, j]))
    new = data.draw(st.sampled_from([x for x in S if x is not old]))
    if old in (S.DETECT, S.GT) and new in (S.DETECT, S.GT):
        return  # indistinguishable by invariants
    if old.name.startswith("MISSING_") and new.name.startswith("MISSING_"):
        return
    s.iloc[i, j] = new.value
    with pytest.raises(ValueError):
        WideTable(v, s, d)
