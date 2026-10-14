"""Expected-outcome sidecar model for generated fixtures (`truth.json`)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

GENERATOR_VERSION = 1
DEFAULT_SEED = 20260101

# Case ids every `project_basic` must contain; addendum-driven ones are tagged in descriptions.
REQUIRED_CASE_IDS: tuple[str, ...] = (
    "a11_nd_differing_dls",
    "a12_nd_dl_zero",
    "a12_nd_lt_zero",
    "a5_gt_fails_min_obs",
    "date_ambiguous_dmy",
    "date_excel_serial",
    "date_mixed_formats",
    "decimal_comma",
    "detect_flag_mdl_rl",
    "dissolved_gt_total",
    "duplicate_detect_vs_detect",
    "duplicate_detect_vs_nd",
    "duplicate_nd_vs_nd",
    "eh_negative",
    "fraction_dtn",
    "high_dl",
    "location_aliases",
    "long_dissolved_total_pair",
    "method_key_merged",
    "method_key_split",
    "no3_as_n",
    "no3_as_no3",
    "no_result_tokens",
    "ph_su",
    "poor_ion_balance",
    "ppm_in_water",
    "q9_qc_field_duplicate",
    "qc_lab",
    "qc_trip_blank_detect",
    "qualifier_code_two_files",
    "qualifier_variants",
    "right_censored",
    "sparse_all_nd_feature",
    "sparse_below_min_obs",
    "spelling_variants",
    "transposed_wide",
    "unknown_analytes",
    "zeros_allow_negative",
    "zeros_log",
    "blank_result_nd",
)


@dataclass(frozen=True)
class AssumedConfig:
    """Config values the generator's data relies on. The generator never reads config files;
    `tests/test_fixture_generator.py` checks these against `tests/fixtures/config/`."""

    qualifier_codes: tuple[str, ...] = ("U", "J", "R")
    lab_b_source_pattern: str = "*lab_b*"
    lab_b_date_format: str = "%d/%m/%Y"
    lab_b_file_name: str = "lab_b_long.csv"
    header_example: str = "Manganese (Dissolved) mg/L"
    header_example_parts: tuple[str, str, str] = ("Manganese", "Dissolved", "mg/L")
    units: tuple[str, ...] = ("mg/L", "ug/L", "mg/L as N", "ppm", "S.U.")
    no_result_tokens: tuple[str, ...] = ("ND", "NM", "NA", "---")
    analyte_keys: tuple[str, ...] = ("mn", "fe", "no3", "ph", "eh")
    no3_as_n_factor: float = 4.426
    min_obs_per_cell: int = 3
    nd_detect_flag: str = "ND"
    detect_detect_flag: str = "D"


ASSUMED_CONFIG = AssumedConfig()


@dataclass(frozen=True)
class InjectedCase:
    case_id: str
    description: str
    files: tuple[str, ...]
    expected: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "description": self.description,
            "files": list(self.files),
            "expected": self.expected,
        }


@dataclass(frozen=True)
class Truth:
    name: str
    seed: int
    cases: tuple[InjectedCase, ...]
    generator_version: int = GENERATOR_VERSION

    def case_ids(self) -> tuple[str, ...]:
        return tuple(c.case_id for c in self.cases)

    def to_json(self) -> str:
        doc = {
            "name": self.name,
            "seed": self.seed,
            "generator_version": self.generator_version,
            "cases": [c.to_dict() for c in sorted(self.cases, key=lambda c: c.case_id)],
        }
        return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Truth:
        doc = json.loads(text)
        return cls(
            name=doc["name"],
            seed=int(doc["seed"]),
            generator_version=int(doc["generator_version"]),
            cases=tuple(
                InjectedCase(
                    case_id=c["case_id"],
                    description=c["description"],
                    files=tuple(c["files"]),
                    expected=dict(c["expected"]),
                )
                for c in doc["cases"]
            ),
        )
