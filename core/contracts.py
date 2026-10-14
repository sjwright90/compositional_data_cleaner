"""Pandera schemas for DataFrame contracts at stage boundaries (spec §5.2, §4.1 Rules)."""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa

from core.artifacts import ALL_STATUSES
from core.ids import Fraction

RAW_LONG = pa.DataFrameSchema(
    {
        "location_id": pa.Column(nullable=True),
        "sample_date": pa.Column(nullable=True),
        "source_file": pa.Column(str),
        "source_sheet": pa.Column(nullable=True),
        "source_row": pa.Column(int),
        "analyte_raw": pa.Column(nullable=True),
        "fraction_hint": pa.Column(nullable=True),
        "unit_raw": pa.Column(nullable=True),
        "ingest_group_id": pa.Column(str),
        "raw_value": pa.Column(nullable=True),
        "value": pa.Column(float, nullable=True),
        "qualifier": pa.Column(nullable=True),
        "detect_flag_raw": pa.Column(nullable=True),
        "dl_mdl_raw": pa.Column(nullable=True),
        "dl_rl_raw": pa.Column(nullable=True),
        "censor": pa.Column(nullable=True, checks=pa.Check.isin(["<", ">", ""])),
    },
    strict=False,
    name="raw_long",
)

CLEAN_LONG = pa.DataFrameSchema(
    {
        "location_id": pa.Column(nullable=False),
        "sample_date": pa.Column(nullable=True),
        "sample_type": pa.Column(nullable=True),
        "feature_id": pa.Column(str),
        "analyte": pa.Column(str),
        "fraction": pa.Column(str, checks=pa.Check.isin([f.value for f in Fraction])),
        "variant": pa.Column(str),
        "unit": pa.Column(str),
        "value": pa.Column(float, nullable=True),
        "cell_status": pa.Column(str, checks=pa.Check.isin(list(ALL_STATUSES))),
        "dl": pa.Column(float, nullable=True),
        "dl_type": pa.Column(nullable=True),
        "flag_action": pa.Column(nullable=True),
    },
    strict=False,
    name="clean_long",
)

METADATA = pa.DataFrameSchema(
    {"location_id": pa.Column(str, unique=True)},
    strict=False,
    name="metadata",
)

_STATUS = pa.DataFrameSchema(
    {".*": pa.Column(str, checks=pa.Check.isin(list(ALL_STATUSES)), regex=True)},
    name="wide_status",
)
_NUMERIC = pa.DataFrameSchema(
    {".*": pa.Column(float, nullable=True, regex=True)},
    name="wide_numeric",
)


def validate_raw_long(df: pd.DataFrame) -> pd.DataFrame:
    return RAW_LONG.validate(df)


def validate_clean_long(df: pd.DataFrame) -> pd.DataFrame:
    return CLEAN_LONG.validate(df)


def validate_metadata(df: pd.DataFrame) -> pd.DataFrame:
    return METADATA.validate(df)


def validate_wide(values: pd.DataFrame, status: pd.DataFrame, dl: pd.DataFrame) -> None:
    """Boundary check for the three WideTable frames: dtypes, status vocabulary, alignment."""
    _NUMERIC.validate(values)
    _NUMERIC.validate(dl)
    _STATUS.validate(status.astype(object).astype(str))
    for name, other in (("status", status), ("dl", dl)):
        if not values.index.equals(other.index) or not values.columns.equals(other.columns):
            raise pa.errors.SchemaError(_STATUS, values, f"{name} is not aligned with values")
