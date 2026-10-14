"""Identity layer tests (spec §5.3, §9 "Identity tests")."""

from __future__ import annotations

import dataclasses
import inspect
import math
from collections.abc import Sequence

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.ids import (
    Candidate,
    DupGroupId,
    FeatureId,
    Fraction,
    IngestGroupKey,
    LocationId,
    SampleKey,
    normalise_code,
    normalise_token,
    raw_location_key,
)

KEY_COLS = ["analyte_raw", "fraction_hint", "unit_raw"]
BASE_RAW = {"analyte_raw": "Mn", "fraction_hint": "Dissolved", "unit_raw": "µg/L"}

key_text = st.text(min_size=1).filter(lambda s: "|" not in s)
feature_ids = st.builds(
    FeatureId,
    analyte=key_text,
    fraction=st.sampled_from(list(Fraction)),
    unit=key_text,
    variant=st.text().filter(lambda s: "|" not in s),
)


def _gid(raw: dict[str, str]) -> str:
    return IngestGroupKey.from_raw(raw, KEY_COLS).id()


# ---- normalise_token ----------------------------------------------------------------


def test_normalise_token_rules() -> None:
    assert normalise_token("  Mn \t Dissolved ") == "mn dissolved"
    assert normalise_token("µg/L") == "ug/l"
    assert normalise_token("μg/L") == "ug/l"
    assert normalise_token("ＭＮ") == "mn"


@given(st.text())
def test_normalise_token_idempotent(s: str) -> None:
    once = normalise_token(s)
    assert normalise_token(once) == once


def test_normalise_code_uppercases() -> None:
    assert normalise_code(" j ") == "J"
    assert normalise_code("J") == "J"
    assert normalise_code("u  j") == "U J"


def test_raw_location_key_is_normalise_token() -> None:
    assert raw_location_key("  MW-1 A ") == normalise_token("MW-1 A")


# ---- Fraction / FeatureId -----------------------------------------------------------


def test_fraction_members() -> None:
    assert [f.value for f in Fraction] == ["D", "T", "N"]
    assert Fraction("D") is Fraction.D


def test_feature_canonical_form() -> None:
    f = FeatureId("mn", Fraction.D, "mg/L")
    assert f.canonical() == "mn|D|mg/L|"
    assert FeatureId("mn", Fraction.T, "mg/L", "icp").canonical() == "mn|T|mg/L|icp"


@given(feature_ids)
def test_feature_round_trip(f: FeatureId) -> None:
    assert FeatureId.parse(f.canonical()) == f


@pytest.mark.parametrize("bad", ["", "mn|D|mg/L", "mn|D|mg/L|v|x", "mn|Z|mg/L|"])
def test_feature_parse_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        FeatureId.parse(bad)


def test_feature_rejects_pipe_in_fields() -> None:
    with pytest.raises(ValueError):
        FeatureId("a|b", Fraction.D, "mg/L")
    with pytest.raises(ValueError):
        FeatureId("a", Fraction.D, "mg/L", "v|w")


def test_feature_fraction_and_variant_distinguish() -> None:
    d = FeatureId("mn", Fraction.D, "mg/L")
    assert d != FeatureId("mn", Fraction.T, "mg/L")
    assert d != FeatureId("mn", Fraction.D, "mg/L", "icp")
    assert d.canonical() != FeatureId("mn", Fraction.T, "mg/L").canonical()


def test_feature_hashable_and_ordered() -> None:
    a = FeatureId("a", Fraction.D, "mg/L")
    b = FeatureId("b", Fraction.D, "mg/L")
    assert {a: 1}[FeatureId("a", Fraction.D, "mg/L")] == 1
    assert sorted([b, a]) == [a, b]


# ---- IngestGroupKey -----------------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        {"analyte_raw": "  mn ", "fraction_hint": "DISSOLVED", "unit_raw": "µg/L"},
        {"analyte_raw": "MN", "fraction_hint": "dissolved", "unit_raw": "μg/L"},
        {"analyte_raw": "Mn", "fraction_hint": "Dissolved", "unit_raw": "ug/l"},
        {"analyte_raw": "Mn", "fraction_hint": "Dissolved ", "unit_raw": " µg/L"},
    ],
)
def test_ingest_group_id_ignores_whitespace_case_micro(variant: dict[str, str]) -> None:
    assert _gid(variant) == _gid(BASE_RAW)


def test_ingest_group_id_collapses_internal_whitespace() -> None:
    a = dict(BASE_RAW, analyte_raw="Total  Mn")
    b = dict(BASE_RAW, analyte_raw="total\tmn")
    assert _gid(a) == _gid(b)


def test_ingest_group_id_differs_on_value_change() -> None:
    assert _gid(dict(BASE_RAW, analyte_raw="Fe")) != _gid(BASE_RAW)
    assert _gid(dict(BASE_RAW, unit_raw="mg/L")) != _gid(BASE_RAW)


def test_ingest_group_id_independent_of_key_col_order_and_extras() -> None:
    raw = dict(BASE_RAW, value="12.3", note="whatever")
    a = IngestGroupKey.from_raw(raw, KEY_COLS).id()
    b = IngestGroupKey.from_raw(raw, list(reversed(KEY_COLS))).id()
    assert a == b == _gid(BASE_RAW)


def test_ingest_group_id_not_confusable_by_delimiters() -> None:
    a = IngestGroupKey.from_raw({"a": "x|b=y", "b": ""}, ["a", "b"]).id()
    b = IngestGroupKey.from_raw({"a": "x", "b": "y"}, ["a", "b"]).id()
    assert a != b


def test_ingest_group_missing_value_is_empty() -> None:
    cols = ["analyte_raw", "unit_raw"]
    k = IngestGroupKey.from_raw({"analyte_raw": None, "unit_raw": "mg/L"}, cols)
    assert ("analyte_raw", "") in k.items
    k2 = IngestGroupKey.from_raw({"unit_raw": "mg/L"}, cols)
    assert k.id() == k2.id()


def test_ingest_group_id_shape_and_determinism() -> None:
    gid = _gid(BASE_RAW)
    assert len(gid) == 16 and int(gid, 16) >= 0
    assert gid == _gid(dict(BASE_RAW))


# ---- No display-name input path -----------------------------------------------------

_DISPLAY_WORDS = ("label", "display", "name", "title", "header")


@pytest.mark.parametrize(
    "fn",
    [
        IngestGroupKey.from_raw,
        FeatureId.__init__,
        FeatureId.parse,
        DupGroupId.from_candidates,
        SampleKey.__init__,
        LocationId.__init__,
    ],
)
def test_no_display_parameters_on_id_constructors(fn: object) -> None:
    params = [p for p in inspect.signature(fn).parameters if p != "self"]  # type: ignore[arg-type]
    assert not [p for p in params if any(w in p.lower() for w in _DISPLAY_WORDS)]


def test_id_dataclass_fields_have_no_display_fields() -> None:
    for cls in (FeatureId, IngestGroupKey, SampleKey, LocationId, DupGroupId):
        names = [f.name for f in dataclasses.fields(cls)]
        assert not [n for n in names if any(w in n.lower() for w in _DISPLAY_WORDS)], cls


def test_non_key_columns_cannot_change_ids() -> None:
    base = IngestGroupKey.from_raw(BASE_RAW, KEY_COLS).id()
    renamed = dict(BASE_RAW, analyte_label="Manganese (renamed)", plot_label="x")
    assert IngestGroupKey.from_raw(renamed, KEY_COLS).id() == base


# ---- SampleKey / LocationId ---------------------------------------------------------


def test_sample_key_hashable_ordered() -> None:
    a = SampleKey(("mw-1", "2020-01-01"))
    b = SampleKey(("mw-1", "2020-02-01"))
    assert {a: 1}[SampleKey(("mw-1", "2020-01-01"))] == 1
    assert sorted([b, a]) == [a, b]


def test_location_id_value_object() -> None:
    assert LocationId("MW-1") == LocationId("MW-1")
    assert LocationId("MW-1") != LocationId("MW-2")
    assert len({LocationId("MW-1"), LocationId("MW-1")}) == 1


# ---- DupGroupId ---------------------------------------------------------------------

SK = SampleKey(("mw-1", "2020-01-01"))
FID = FeatureId("mn", Fraction.D, "mg/L")
CANDS: list[Candidate] = [
    (1.0, "DETECT", None, ()),
    (0.5, "ND", 0.5, ("u",)),
    (2.0, "DETECT", None, ("j", "b")),
]


def _dup(cands: Sequence[Candidate]) -> DupGroupId:
    return DupGroupId.from_candidates(SK, FID, cands)


def test_dup_group_stable_under_reordering() -> None:
    assert _dup(CANDS) == _dup(list(reversed(CANDS)))
    assert _dup(CANDS) == _dup([CANDS[1], CANDS[2], CANDS[0]])


@given(st.permutations(CANDS))
def test_dup_group_permutation_property(perm: list[Candidate]) -> None:
    assert _dup(perm) == _dup(CANDS)


def test_dup_group_code_order_and_case_insensitive() -> None:
    a = [(2.0, "DETECT", None, ("j", "b"))]
    b = [(2.0, "DETECT", None, (" B ", "J"))]
    assert _dup(a) == _dup(b)


@pytest.mark.parametrize(
    "idx,new",
    [
        (0, (1.1, "DETECT", None, ())),
        (0, (1.0, "GT", None, ())),
        (1, (0.5, "ND", 0.4, ("u",))),
        (1, (0.5, "ND", 0.5, ("x",))),
    ],
)
def test_dup_group_changes_when_candidate_changes(idx: int, new: Candidate) -> None:
    changed = list(CANDS)
    changed[idx] = new
    assert _dup(changed) != _dup(CANDS)


def test_dup_group_changes_with_candidate_set_membership() -> None:
    assert _dup(CANDS[:2]) != _dup(CANDS)


def test_dup_group_depends_on_sample_and_feature() -> None:
    other_sk = SampleKey(("mw-2", "2020-01-01"))
    other_f = FeatureId("fe", Fraction.D, "mg/L")
    base = _dup(CANDS)
    assert DupGroupId.from_candidates(other_sk, FID, CANDS) != base
    assert DupGroupId.from_candidates(SK, other_f, CANDS) != base
    assert base.sample_key == SK and base.feature_id == FID


def test_dup_group_nan_and_none_dl_are_deterministic() -> None:
    a = [(math.nan, "MISSING_BAD", None, ())]
    b = [(math.nan, "MISSING_BAD", None, ())]
    assert _dup(a) == _dup(b)
    assert len(_dup(a).candidates_hash) == 64


def test_dup_group_hashable() -> None:
    assert len({_dup(CANDS), _dup(list(reversed(CANDS)))}) == 1
