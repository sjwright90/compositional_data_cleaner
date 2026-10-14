"""Synthetic water-chemistry fixture generator.

    python -m tests.fixtures.generate [--name project_basic] [--seed N] [--out DIR]

Writes `<out>/<name>/` (default `tests/fixtures/generated/`, gitignored) with source files and
`truth.json`. The generator never reads config files; values it relies on live in
`tests.fixtures.truth.ASSUMED_CONFIG` and are checked against `tests/fixtures/config/` by tests.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from openpyxl import Workbook  # type: ignore[import-untyped]

from tests.fixtures.truth import (
    ASSUMED_CONFIG,
    DEFAULT_SEED,
    REQUIRED_CASE_IDS,
    InjectedCase,
    Truth,
)

log = logging.getLogger(__name__)

LAB_A = "lab_a_long.xlsx"
LAB_B = ASSUMED_CONFIG.lab_b_file_name
LAB_C = "lab_c_wide_transposed.xlsx"
LAB_D = "lab_d_wide_decimal_comma.csv"
LAB_E = "lab_e_wide.xlsx"
TRUTH = "truth.json"

LONG_COLS: tuple[str, ...] = (
    "sample_id",
    "location",
    "date",
    "analyte",
    "fraction",
    "unit",
    "result",
    "qualifier",
    "detect_flag",
    "mdl",
    "rl",
    "sample_type",
    "parent_sample_id",
    "method",
)

_BASE_DATE = date(2023, 1, 9)
_FIXED_TS = datetime(2024, 1, 1, 0, 0, 0)

# equivalent weights (mg/meq) for the deterministic ion-balance sample
_EQ_WEIGHT = {
    "Calcium": 20.04,
    "Magnesium": 12.15,
    "Sodium": 22.99,
    "Potassium": 39.10,
    "Chloride": 35.45,
    "Sulfate": 48.03,
    "Bicarbonate": 61.02,
}
_CATIONS = ("Calcium", "Magnesium", "Sodium", "Potassium")


def _iso(n: int) -> str:
    return (_BASE_DATE + timedelta(days=n)).isoformat()


def _num(x: float) -> str:
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


class _Long:
    """Accumulates long-shape rows; every row is a dict over `LONG_COLS`."""

    def __init__(self, prefix: str) -> None:
        self.rows: list[dict[str, Any]] = []
        self._prefix = prefix
        self._n = 0

    def new_sample(self) -> str:
        self._n += 1
        return f"{self._prefix}{self._n:04d}"

    def add(
        self,
        location: str,
        when: Any,
        analyte: str,
        result: str,
        *,
        fraction: str = "Dissolved",
        unit: str = "mg/L",
        sample_id: str | None = None,
        **extra: str,
    ) -> str:
        sid = sample_id or self.new_sample()
        row: dict[str, Any] = {c: "" for c in LONG_COLS}
        row.update(
            sample_id=sid,
            location=location,
            date=when,
            analyte=analyte,
            fraction=fraction,
            unit=unit,
            result=result,
            **extra,
        )
        self.rows.append(row)
        return sid


class _Cases:
    def __init__(self) -> None:
        self._cases: dict[str, InjectedCase] = {}

    def add(self, case_id: str, description: str, files: Sequence[str], **expected: Any) -> None:
        if case_id in self._cases:
            raise ValueError(f"duplicate case id {case_id}")
        self._cases[case_id] = InjectedCase(case_id, description, tuple(files), dict(expected))

    def all(self) -> tuple[InjectedCase, ...]:
        return tuple(self._cases[k] for k in sorted(self._cases))


def _detects(rng: np.random.Generator, n: int, lo: float, hi: float) -> list[str]:
    return [_num(float(v)) for v in np.round(rng.uniform(lo, hi, n), 3)]


def _build_lab_a(rng: np.random.Generator, cases: _Cases) -> _Long:
    a = _Long("A")
    f = (LAB_A,)

    # background coverage (not a case): 4 locations x 3 features x 4 dates, ~10% non-detect
    for loc in ("BG-1", "BG-2", "BG-3", "BG-4"):
        for analyte, unit, dl in (("Manganese", "mg/L", 0.05), ("Iron", "mg/L", 0.05)):
            for k in range(4):
                v = float(np.round(rng.lognormal(-1.0, 0.6), 3))
                res = f"<{_num(dl)}" if rng.uniform() < 0.1 else _num(max(v, dl * 1.5))
                a.add(loc, _iso(k * 30), analyte, res, unit=unit)

    # Mn dissolved and total as separate features at one location
    start = len(a.rows)
    for k in range(3):
        d = float(np.round(rng.uniform(0.2, 0.6), 3))
        a.add("MW-3", _iso(k * 30), "Manganese", _num(d), fraction="Dissolved")
        a.add("MW-3", _iso(k * 30), "Manganese", _num(d + 0.1), fraction="Total")
    cases.add(
        "long_dissolved_total_pair",
        "Mn as both Dissolved and Total at MW-3 (separate features, Mn-D != Mn-T)",
        f,
        rows=len(a.rows) - start,
        features=2,
    )

    # method as extra ingest key: one pair merges after normalisation, one pair stays split
    start = len(a.rows)
    for k, m in enumerate(("ICP-MS", "icp-ms ")):
        a.add(
            "MW-4",
            _iso(k * 30),
            "Iron",
            _num(float(rng.uniform(100, 300))),
            fraction="Total",
            unit="ug/L",
            method=m,
        )
    cases.add(
        "method_key_merged",
        "Fe-T ug/L at MW-4 with method 'ICP-MS' and 'icp-ms ' (one ingest group)",
        f,
        rows=len(a.rows) - start,
        groups=1,
    )
    start = len(a.rows)
    for k, m in enumerate(("EPA 200.7", "EPA 200.8")):
        a.add(
            "MW-4",
            _iso(k * 30),
            "Manganese",
            _num(float(rng.uniform(50, 150))),
            fraction="Total",
            unit="ug/L",
            method=m,
        )
    cases.add(
        "method_key_split",
        "Mn-T ug/L at MW-4 with method 'EPA 200.7' vs 'EPA 200.8' (two ingest groups)",
        f,
        rows=len(a.rows) - start,
        groups=2,
    )

    # spelling / whitespace / micro-sign variants that merge into one group each
    start = len(a.rows)
    for k, (an, un) in enumerate(
        (("Manganese", "mg/L"), ("manganese ", "mg/l"), ("MANGANESE", " mg/L"), ("Mn", "mg/L "))
    ):
        a.add(
            "MW-2", _iso(k * 30), an, _num(float(rng.uniform(0.2, 0.5))), fraction="Total", unit=un
        )
    for k, un in enumerate(("ug/L", "µg/L", "μg/L")):
        a.add("MW-2", _iso(k * 30), "Iron", _num(float(rng.uniform(100, 300))), unit=un)
    cases.add(
        "spelling_variants",
        "Mn-T mg/L (case/space/alias variants) and Fe-D ug/L (ug, micro sign, Greek mu) at MW-2",
        f,
        rows=len(a.rows) - start,
        groups=2,
    )

    # location aliases
    start = len(a.rows)
    for k, loc in enumerate(("MW-1", "MW1", "MW-01")):
        for j in range(2):
            a.add(loc, _iso(k * 7 + j * 90), "Iron", _num(float(rng.uniform(0.2, 0.8))))
    cases.add(
        "location_aliases",
        "Fe-D at MW-1, MW1 and MW-01 (one location)",
        f,
        rows=len(a.rows) - start,
        locations=["MW-1", "MW-01", "MW1"],
    )

    # analytes not in the vocabulary
    start = len(a.rows)
    for an in ("Lithium", "Strontium"):
        for k in range(2):
            a.add(
                "MW-2",
                _iso(k * 30),
                an,
                _num(float(rng.uniform(20, 90))),
                fraction="Total",
                unit="ug/L",
            )
    cases.add(
        "unknown_analytes",
        "Lithium and Strontium (not in the vocabulary)",
        f,
        rows=len(a.rows) - start,
        analytes=["Lithium", "Strontium"],
    )

    # D / T / N fractions
    start = len(a.rows)
    sid = a.new_sample()
    a.add(
        "MW-5",
        _iso(0),
        "Iron",
        _num(float(rng.uniform(0.2, 0.4))),
        fraction="Dissolved",
        sample_id=sid,
    )
    a.add(
        "MW-5", _iso(0), "Iron", _num(float(rng.uniform(0.5, 0.9))), fraction="Total", sample_id=sid
    )
    a.add(
        "MW-5",
        _iso(0),
        "Eh",
        _num(float(rng.uniform(40, 90))),
        fraction="N/A",
        unit="mV",
        sample_id=sid,
    )
    cases.add(
        "fraction_dtn",
        "One sample with D, T and N (non-fraction) analytes at MW-5",
        f,
        rows=len(a.rows) - start,
        fractions=["Dissolved", "N/A", "Total"],
    )

    # pH in S.U.
    start = len(a.rows)
    for k in range(3):
        a.add(
            "MW-5",
            _iso(30 + k * 30),
            "pH",
            _num(float(np.round(rng.uniform(6.5, 7.8), 2))),
            fraction="N/A",
            unit="S.U.",
        )
    cases.add("ph_su", "pH in S.U. (no fraction) at MW-5", f, rows=len(a.rows) - start)

    # dates: mixed formats and Excel serials (numeric cells), file-level
    start = len(a.rows)
    a.add("MW-6", "2023-02-01", "Manganese", _num(float(rng.uniform(0.2, 0.5))))
    a.add("MW-6", datetime(2023, 3, 1), "Manganese", _num(float(rng.uniform(0.2, 0.5))))
    a.add("MW-6", "15-Mar-2023", "Manganese", _num(float(rng.uniform(0.2, 0.5))))
    cases.add(
        "date_mixed_formats",
        "ISO text, native datetime cell and 'dd-Mon-yyyy' text at MW-6",
        f,
        rows=len(a.rows) - start,
        formats=["datetime_cell", "dd-Mon-yyyy", "iso_text"],
    )
    start = len(a.rows)
    for serial in (44927, 44958, 44986):  # 2023-01-01, 2023-02-01, 2023-03-01
        a.add("MW-6", serial, "Iron", _num(float(rng.uniform(0.2, 0.5))))
    cases.add(
        "date_excel_serial",
        "Excel serial dates as numeric cells at MW-6",
        f,
        rows=len(a.rows) - start,
        serials=[44927, 44958, 44986],
    )

    # qualifier variants
    start = len(a.rows)
    q = "MW-7"
    a.add(q, _iso(0), "Manganese", "<0.5")
    a.add(q, _iso(7), "Manganese", "0.5 J")
    a.add(q, _iso(14), "Manganese", "0.5J")
    a.add(q, _iso(21), "Manganese", "ND", mdl="0.5")
    a.add(q, _iso(28), "Manganese", "0.8", qualifier="R")
    a.add(q, _iso(35), "Manganese", "0.9", qualifier="U")
    cases.add(
        "qualifier_variants",
        "Qualifier variants at MW-7: '<0.5', '0.5 J', '0.5J', 'ND' with MDL, qualifier R and U",
        f,
        rows=len(a.rows) - start,
        variants=["0.5 J", "0.5J", "<0.5", "ND", "qualifier=R", "qualifier=U"],
    )

    # detect flag with MDL and RL; blank-result non-detects; A12 DL = 0
    start = len(a.rows)
    a.add(q, _iso(42), "Iron", "0.9", detect_flag="D", mdl="0.05", rl="0.2")
    a.add(q, _iso(49), "Iron", "0.2", detect_flag="ND", mdl="0.05", rl="0.2")
    a.add(q, _iso(56), "Iron", "0.45", detect_flag="D", mdl="0.05", rl="0.2")
    cases.add(
        "detect_flag_mdl_rl",
        "Separate detect_flag, MDL and RL columns at MW-7",
        f,
        rows=len(a.rows) - start,
    )
    start = len(a.rows)
    a.add(q, _iso(63), "Iron", "", detect_flag="ND", mdl="0.05", rl="0.2")
    a.add(q, _iso(70), "Iron", "", detect_flag="ND", mdl="0.1", rl="0.2")
    cases.add(
        "blank_result_nd",
        "Blank result with detect_flag ND and a positive MDL (non-detect at the MDL) at MW-7",
        f,
        rows=len(a.rows) - start,
    )
    start = len(a.rows)
    a.add(q, _iso(77), "Iron", "", detect_flag="ND", mdl="0", rl="0.2")
    a.add(q, _iso(84), "Iron", "", detect_flag="ND", mdl="0", rl="0.2")
    cases.add(
        "a12_nd_dl_zero",
        "A12: non-detect with DL = 0 given via the MDL column (blank result) at MW-7",
        f,
        rows=len(a.rows) - start,
    )
    start = len(a.rows)
    a.add("MW-10", _iso(0), "Manganese", "<0")
    a.add("MW-10", _iso(30), "Manganese", "<0")
    cases.add(
        "a12_nd_lt_zero",
        "A12: non-detect written '<0' at MW-10",
        f,
        rows=len(a.rows) - start,
    )

    # right-censored
    start = len(a.rows)
    a.add("MW-11", _iso(0), "Iron", ">2000", unit="ug/L")
    a.add("MW-11", _iso(30), "Iron", ">2000", unit="ug/L")
    cases.add(
        "right_censored",
        "Right-censored '>2000' values at MW-11",
        f,
        rows=len(a.rows) - start,
    )

    # QC samples (Q9 field duplicate with parent link, trip blank detect, lab QC)
    parent = a.new_sample()
    dup = a.new_sample()
    for an in ("Manganese", "Iron"):
        a.add(
            "MW-2",
            _iso(120),
            an,
            _num(float(rng.uniform(0.3, 0.6))),
            sample_id=parent,
            sample_type="Normal",
        )
    for an in ("Manganese", "Iron"):
        a.add(
            "MW-2",
            _iso(120),
            an,
            _num(float(rng.uniform(0.3, 0.6))),
            sample_id=dup,
            sample_type="Field Duplicate",
            parent_sample_id=parent,
        )
    cases.add(
        "q9_qc_field_duplicate",
        "Q9: field duplicate FD sample linked to its parent sample at MW-2",
        f,
        rows=4,
        sample_id=dup,
        parent_sample_id=parent,
    )
    tb = a.new_sample()
    a.add("MW-2", _iso(120), "Manganese", "0.05", sample_id=tb, sample_type="Trip Blank")
    cases.add(
        "qc_trip_blank_detect",
        "Trip blank (sample_type 'Trip Blank') with a detect at MW-2",
        f,
        rows=1,
        sample_id=tb,
    )
    start = len(a.rows)
    for an in ("Manganese", "Iron"):
        a.add("LAB", _iso(120), an, _num(float(rng.uniform(0.9, 1.1))), sample_type="Lab QC")
    cases.add("qc_lab", "Lab QC samples at location LAB", f, rows=len(a.rows) - start)

    # zeros
    start = len(a.rows)
    a.add("MW-12", _iso(0), "Manganese", "0")
    a.add("MW-12", _iso(30), "Manganese", "0")
    cases.add(
        "zeros_log", "True zeros for Mn (transform = log) at MW-12", f, rows=len(a.rows) - start
    )
    start = len(a.rows)
    a.add("MW-12", _iso(0), "Eh", "0", fraction="N/A", unit="mV")
    a.add("MW-12", _iso(30), "Eh", "0", fraction="N/A", unit="mV")
    cases.add(
        "zeros_allow_negative",
        "True zeros for Eh (allow_negative) at MW-12",
        f,
        rows=len(a.rows) - start,
    )

    # Eh negatives
    start = len(a.rows)
    for k, v in enumerate((-150.0, -220.0, 35.0)):
        a.add(
            "MW-13",
            _iso(k * 30),
            "Eh",
            _num(v + float(np.round(rng.uniform(-10, 10), 1))),
            fraction="N/A",
            unit="mV",
        )
    cases.add("eh_negative", "Eh with negative values at MW-13", f, rows=len(a.rows) - start)

    # duplicates
    loc = "MW-14"
    for case_id, desc, pair, when in (
        ("duplicate_detect_vs_nd", "same key: a detect and a non-detect", ("0.30", "<0.05"), 0),
        ("duplicate_detect_vs_detect", "same key: two detects", ("0.30", "0.34"), 30),
        (
            "duplicate_nd_vs_nd",
            "same key: two non-detects with different DLs",
            ("<0.05", "<0.10"),
            60,
        ),
    ):
        for r in pair:
            a.add(loc, _iso(when), "Iron", r)
        cases.add(case_id, f"Duplicate rows at MW-14, {desc}", f, rows=2, values=list(pair))

    # sparse coverage
    start = len(a.rows)
    a.add("MW-9", _iso(0), "Manganese", _num(float(rng.uniform(0.2, 0.5))))
    a.add("MW-9", _iso(30), "Manganese", _num(float(rng.uniform(0.2, 0.5))))
    for k in range(3):
        a.add("MW-9", _iso(k * 30), "Iron", _num(float(rng.uniform(0.2, 0.5))))
    cases.add(
        "sparse_below_min_obs",
        "MW-9 has only 2 Mn observations (below min_obs_per_cell = 3) but 3 Fe",
        f,
        location="MW-9",
        analyte="Manganese",
        rows=2,
    )
    for k in range(3):
        a.add("MW-8", _iso(k * 30), "Manganese", _num(float(rng.uniform(0.2, 0.5))))
    for k, r in enumerate(("<0.05", "<0.05", "<0.1")):
        a.add("MW-8", _iso(k * 30), "Iron", r)
    cases.add(
        "sparse_all_nd_feature",
        "MW-8: Fe-D is non-detect in every observation while Mn is detected",
        f,
        location="MW-8",
        analyte="Iron",
        rows=3,
    )
    a.add("MW-15", _iso(0), "Manganese", _num(float(rng.uniform(0.2, 0.5))))
    a.add("MW-15", _iso(30), "Manganese", "<0.05")
    a.add("MW-15", _iso(60), "Manganese", ">5")
    cases.add(
        "a5_gt_fails_min_obs",
        "A5: MW-15 Mn has 3 rows (1 detect, 1 ND, 1 right-censored): 3 values, only 2 count"
        " toward min_obs_per_cell",
        f,
        location="MW-15",
        analyte="Manganese",
        rows=3,
        gt_rows=1,
    )

    # high-DL
    start = len(a.rows)
    for k, s in enumerate(_detects(rng, 3, 0.02, 0.04)):
        a.add("MW-16", _iso(k * 30), "Manganese", s)
    a.add("MW-16", _iso(90), "Manganese", "<5")
    cases.add(
        "high_dl",
        "A non-detect with DL 5 far above the detects (<= 0.04) at MW-16",
        f,
        rows=len(a.rows) - start,
        dl=5,
    )

    # A11: NDs for one feature in >= 2 locations with different DLs incl. a very small one
    dls = (0.05, 0.5, 0.0005)
    locs = ("MW-17", "MW-18", "MW-19")
    for loc_, dl in zip(locs, dls, strict=True):
        a.add(loc_, _iso(0), "Iron", f"<{_num(dl)}")
        a.add(loc_, _iso(30), "Iron", _num(dl * 4))
    cases.add(
        "a11_nd_differing_dls",
        "A11: Fe-D non-detects at MW-17/18/19 with DLs 0.05, 0.5 and 0.0005",
        f,
        analyte="Iron",
        locations=list(locs),
        dls=list(dls),
        rows=3,
    )

    # NO3 reporting bases
    start = len(a.rows)
    for k, s in enumerate(_detects(rng, 3, 1.0, 3.0)):
        a.add("MW-20", _iso(k * 30), "Nitrate", s, fraction="N/A", unit="mg/L as N")
    cases.add(
        "no3_as_n",
        "Nitrate reported in mg/L as N at MW-20 (analyte-specific factor to mg/L NO3)",
        f,
        rows=len(a.rows) - start,
        factor=ASSUMED_CONFIG.no3_as_n_factor,
    )
    start = len(a.rows)
    for k, s in enumerate(_detects(rng, 3, 4.0, 12.0)):
        a.add("MW-20", _iso(90 + k * 30), "Nitrate as NO3", s, fraction="N/A", unit="mg/L")
    cases.add(
        "no3_as_no3",
        "Nitrate reported as NO3 in mg/L at MW-20",
        f,
        rows=len(a.rows) - start,
    )

    # ppm in water
    start = len(a.rows)
    for k, s in enumerate(_detects(rng, 3, 0.2, 0.6)):
        a.add("MW-21", _iso(k * 30), "Iron", s, unit="ppm")
    cases.add(
        "ppm_in_water",
        "Fe-D reported in ppm for a water sample at MW-21",
        f,
        rows=len(a.rows) - start,
    )

    # poor ion balance (fixed concentrations) and dissolved > total (fixed)
    conc = {
        "Calcium": 80.0,
        "Magnesium": 20.0,
        "Sodium": 50.0,
        "Potassium": 5.0,
        "Chloride": 10.0,
        "Sulfate": 20.0,
        "Bicarbonate": 60.0,
    }
    ib = a.new_sample()
    for an, v in conc.items():
        a.add("MW-22", _iso(0), an, _num(v), sample_id=ib)
    cat = sum(v / _EQ_WEIGHT[k] for k, v in conc.items() if k in _CATIONS)
    ani = sum(v / _EQ_WEIGHT[k] for k, v in conc.items() if k not in _CATIONS)
    pct = round(100 * (cat - ani) / (cat + ani), 1)
    cases.add(
        "poor_ion_balance",
        "One sample at MW-22 with a cation/anion imbalance far above any tolerance",
        f,
        rows=len(conc),
        sample_id=ib,
        imbalance_pct=pct,
    )
    dt = a.new_sample()
    a.add("MW-24", _iso(0), "Iron", "1.2", fraction="Dissolved", sample_id=dt)
    a.add("MW-24", _iso(0), "Iron", "0.8", fraction="Total", sample_id=dt)
    cases.add(
        "dissolved_gt_total",
        "One sample at MW-24 with Fe dissolved 1.2 > Fe total 0.8",
        f,
        rows=2,
        sample_id=dt,
        dissolved=1.2,
        total=0.8,
    )
    return a


def _build_lab_b(rng: np.random.Generator, cases: _Cases) -> _Long:
    b = _Long("B")
    f = (LAB_B,)

    def dmy(day: int, month: int) -> str:
        return f"{day:02d}/{month:02d}/2023"

    for d, m in ((3, 4), (5, 6), (1, 2)):
        b.add("LB-1", dmy(d, m), "Manganese", _num(float(rng.uniform(0.2, 0.6))))
    for d, m in ((2, 3), (4, 5)):
        b.add("LB-2", dmy(d, m), "Iron", "0.1", qualifier="U")
    for tok, (d, m) in zip(
        ASSUMED_CONFIG.no_result_tokens, ((6, 7), (7, 8), (8, 9), (9, 10)), strict=True
    ):
        b.add("LB-3", dmy(d, m), "Manganese", tok)
    cases.add(
        "date_ambiguous_dmy",
        "lab_b file: every date has day <= 12 so day/month order is ambiguous (rule: dd/mm/yyyy)",
        f,
        rows=len(b.rows),
        ambiguous_dates=len(b.rows),
    )
    cases.add(
        "no_result_tokens",
        "No-result tokens ND, NM, NA and --- with no DL columns at LB-3",
        f,
        rows=len(ASSUMED_CONFIG.no_result_tokens),
        tokens=list(ASSUMED_CONFIG.no_result_tokens),
    )
    return b


def _write_xlsx(
    path: Path, header: Sequence[str], rows: Sequence[Sequence[Any]], title: str
) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = title
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    wb.properties.creator = "fixture-generator"
    wb.properties.created = _FIXED_TS
    wb.properties.modified = _FIXED_TS
    wb.save(path)


def _write_csv(
    path: Path, header: Sequence[str], rows: Sequence[Sequence[Any]], delimiter: str = ","
) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=delimiter, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def _long_matrix(long: _Long) -> list[list[Any]]:
    return [[r[c] for c in LONG_COLS] for r in long.rows]


def _write_lab_c(path: Path, rng: np.random.Generator, cases: _Cases) -> None:
    """Transposed wide sheet: samples as columns, analytes as rows, optional unit column."""
    samples = [
        ("WELL-A", "2023-05-01"),
        ("WELL-A", "2023-06-01"),
        ("WELL-B", "2023-05-01"),
        ("WELL-B", "2023-06-01"),
    ]
    header = ["Parameter", "Unit"] + [f"S{k + 1}" for k in range(len(samples))]
    rows: list[list[Any]] = [
        ["Location", ""] + [s[0] for s in samples],
        ["Date", ""] + [s[1] for s in samples],
    ]
    analyte_rows = (
        ("Manganese (Dissolved) mg/L", "", 0.1, 0.9),
        ("Iron (Total) mg/L", "", 0.2, 1.5),
        ("pH", "S.U.", 6.5, 7.8),
    )
    for label, unit, lo, hi in analyte_rows:
        vals = [_num(float(np.round(rng.uniform(lo, hi), 2))) for _ in samples]
        if label.startswith("Manganese"):
            vals[1] = "<0.1"
        rows.append([label, unit, *vals])
    _write_xlsx(path, header, rows, "wide_transposed")
    cases.add(
        "transposed_wide",
        "lab_c: samples as columns (transpose), headers 'Analyte (Fraction) unit', unit column",
        (LAB_C,),
        samples=len(samples),
        analyte_rows=len(analyte_rows),
        header_example=ASSUMED_CONFIG.header_example,
    )


def _write_lab_d(path: Path, rng: np.random.Generator, cases: _Cases) -> None:
    """Wide sheet, one row per sample, semicolon-delimited, decimal commas, text dates."""
    header = ["Location", "Date", "Manganese (Dissolved) mg/L", "Iron (Total) ug/L", "pH"]
    rows: list[list[str]] = []
    for k, loc in enumerate(("DC-1", "DC-1", "DC-2", "DC-2", "DC-3", "DC-3")):
        mn = _num(float(np.round(rng.uniform(0.1, 0.9), 2))).replace(".", ",")
        fe = (
            f"{float(np.round(rng.uniform(1000, 9000), 1)):,.1f}".replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
        ph = _num(float(np.round(rng.uniform(6.5, 7.8), 2))).replace(".", ",")
        if k == 1:
            mn = "<0,05"
        rows.append([loc, f"2023-0{k % 3 + 4}-1{k // 3 + 1}", mn, fe, ph])
    _write_csv(path, header, rows, delimiter=";")
    n = sum(len(re.findall(r"\d,\d", c)) for r in rows for c in r)
    cases.add(
        "decimal_comma",
        "lab_d: semicolon-delimited wide sheet with decimal commas, thousands '.' and text dates",
        (LAB_D,),
        rows=len(rows),
        decimal_comma_cells=n,
    )


def _write_lab_e(path: Path, rng: np.random.Generator, cases: _Cases) -> None:
    """Standard wide export: header, units row, detection-limit row, then one row per sample."""
    meta = ["Sample ID", "Location", "Date", "Sample Type"]
    analytes = (
        ("Manganese (Dissolved)", "mg/L", "0.005", (0.01, 0.4)),
        ("Iron (Dissolved)", "mg/L", "0.01", (0.05, 0.9)),
        ("Iron (Total)", "mg/L", "0.01", (0.1, 1.5)),
        ("pH", "S.U.", "", (6.5, 7.8)),
    )
    header = meta + [a[0] for a in analytes]
    units = ["", "", "", ""] + [a[1] for a in analytes]
    dl_row = ["", "", "", ""] + [a[2] for a in analytes]
    samples = (
        ("E0001", "MW-3", "2023-07-03", "Normal"),
        ("E0002", "MW-3", "2023-08-07", "Normal"),
        ("E0003", "BG-2", "2023-07-04", "Normal"),
        ("E0004", "BG-2", "2023-08-08", "Normal"),
        ("E0005", "WELL-E1", "2023-07-05", "Normal"),
        ("E0006", "WELL-E1", "2023-08-09", "Field Duplicate"),
        ("E0007", "WELL-E2", "2023-07-06", "Normal"),
    )
    overrides = {  # (sample row, analyte column) -> cell text
        (1, 0): "<0.005",
        (2, 1): "<0.01",
        (3, 1): "<0.01",
        (3, 2): "",
        (4, 0): "",
        (5, 2): "NM",
        (6, 2): ">1.5",
    }
    rows: list[list[Any]] = []
    for i, s in enumerate(samples):
        cells = []
        for j, (_, _, _, (lo, hi)) in enumerate(analytes):
            nd = 2 if j == 3 else 3
            v = _num(float(np.round(rng.uniform(lo, hi), nd)))
            cells.append(overrides.get((i, j), v))
        rows.append([*s, *cells])
    _write_xlsx(path, header, [units, dl_row, *rows], "wide")
    flat = [c for (_, _), c in overrides.items()]
    cases.add(
        "wide_standard",
        "standard wide export, requested by Sam",
        (LAB_E,),
        samples=len(samples),
        analyte_columns=len(analytes),
        nd_cells=sum(1 for c in flat if c.startswith("<")),
        blank_cells=sum(1 for c in flat if c == ""),
        token_cells=sum(1 for c in flat if c == "NM"),
        gt_cells=sum(1 for c in flat if c.startswith(">")),
        overlap_locations=["BG-2", "MW-3"],
        units_row=True,
        dl_row=True,
        metadata_cells_blank=True,
    )


def generate(name: str, seed: int, out_root: Path) -> Truth:
    """Write fixture project `name` under `out_root/<name>/`; return the injected-case truth."""
    if name != "project_basic":
        raise ValueError(f"unknown fixture project '{name}'; available: project_basic")
    project = Path(out_root) / name
    project.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    cases = _Cases()

    long_a = _build_lab_a(rng, cases)
    long_b = _build_lab_b(rng, cases)
    _write_xlsx(project / LAB_A, LONG_COLS, _long_matrix(long_a), "long")
    _write_csv(project / LAB_B, LONG_COLS, _long_matrix(long_b))
    _write_lab_c(project / LAB_C, rng, cases)
    _write_lab_d(project / LAB_D, rng, cases)
    _write_lab_e(project / LAB_E, rng, cases)

    n_a = sum(1 for r in long_a.rows if r["qualifier"] == "U")
    n_b = sum(1 for r in long_b.rows if r["qualifier"] == "U")
    cases.add(
        "qualifier_code_two_files",
        "Qualifier code 'U' appears in both lab_a and lab_b",
        (LAB_A, LAB_B),
        code="U",
        rows_lab_a=n_a,
        rows_lab_b=n_b,
    )

    truth = Truth(name=name, seed=seed, cases=cases.all())
    missing = sorted(set(REQUIRED_CASE_IDS) - set(truth.case_ids()))
    if missing:
        raise RuntimeError(f"generator did not produce required cases: {missing}")
    (project / "truth.json").write_text(truth.to_json(), encoding="utf-8", newline="\n")
    log.info("wrote %s (%d cases, seed %d)", project, len(truth.cases), seed)
    return truth


def main(argv: Sequence[str] | None = None) -> int:
    default_out = Path(__file__).resolve().parent / "generated"
    p = argparse.ArgumentParser(description="Generate synthetic water-chemistry fixtures.")
    p.add_argument("--name", default="project_basic")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--out", type=Path, default=default_out)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    generate(args.name, args.seed, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
