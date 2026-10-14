"""Identity layer: frozen, hashable value objects used as decision keys (spec §4.1, §5.3).

IDs are built only from normalised keys; display names, labels and headers have no
input path here.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

_WS = re.compile(r"\s+")
_FEATURE_SEP = "|"
_SHORT_HASH_LEN = 16


def normalise_token(s: str) -> str:
    """NFKC, trim, collapse internal whitespace, casefold, µ/μ -> u. Used for all keys."""
    s = unicodedata.normalize("NFKC", s)
    s = _WS.sub(" ", s.strip()).casefold()
    return s.replace("µ", "u").replace("μ", "u")


def normalise_code(code: str) -> str:
    """Qualifier-code key: `normalise_token(code).upper()`."""
    return normalise_token(code).upper()


def raw_location_key(raw: str) -> str:
    """Key of a raw location string in the location dictionary."""
    return normalise_token(raw)


def _sha256(payload: object) -> str:
    blob = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("ascii")).hexdigest()


class Fraction(StrEnum):
    D = "D"
    T = "T"
    N = "N"


@dataclass(frozen=True, order=True)
class FeatureId:
    analyte: str  # vocabulary key, never a raw string
    fraction: Fraction
    unit: str  # target unit key
    variant: str = ""  # empty unless the user splits by an extra ingest key

    def __post_init__(self) -> None:
        for name in ("analyte", "unit", "variant"):
            if _FEATURE_SEP in getattr(self, name):
                raise ValueError(f"FeatureId.{name} must not contain {_FEATURE_SEP!r}")
        object.__setattr__(self, "fraction", Fraction(self.fraction))

    def canonical(self) -> str:
        return _FEATURE_SEP.join((self.analyte, self.fraction.value, self.unit, self.variant))

    @classmethod
    def parse(cls, s: str) -> FeatureId:
        parts = s.split(_FEATURE_SEP)
        if len(parts) != 4:
            raise ValueError(f"Not a feature id (need 4 '|'-separated parts): {s!r}")
        analyte, fraction, unit, variant = parts
        return cls(analyte, Fraction(fraction), unit, variant)


@dataclass(frozen=True)
class IngestGroupKey:
    items: tuple[tuple[str, str], ...]  # sorted (column, normalised value) pairs

    @classmethod
    def from_raw(cls, raw: Mapping[str, str | None], key_cols: Sequence[str]) -> IngestGroupKey:
        pairs = {
            normalise_token(col): normalise_token("" if raw.get(col) is None else str(raw[col]))
            for col in key_cols
        }
        return cls(tuple(sorted(pairs.items())))

    def id(self) -> str:
        return _sha256([list(p) for p in self.items])[:_SHORT_HASH_LEN]


@dataclass(frozen=True, order=True)
class SampleKey:
    parts: tuple[str, ...]  # pivot-index values; default profile: location_id, sample_date (ISO)


@dataclass(frozen=True, order=True)
class LocationId:
    value: str  # canonical id after the location dictionary


Candidate = tuple[float, str, float | None, Iterable[str]]  # (value, cell_status, dl, codes)


def _num_token(x: float | None) -> str:
    if x is None:
        return "none"
    if math.isnan(x):
        return "nan"
    return repr(float(x))


@dataclass(frozen=True)
class DupGroupId:
    sample_key: SampleKey
    feature_id: FeatureId
    candidates_hash: str  # full SHA-256 of the sorted candidate set

    @classmethod
    def from_candidates(
        cls,
        sample_key: SampleKey,
        feature_id: FeatureId,
        candidates: Iterable[Candidate],
    ) -> DupGroupId:
        canon = sorted(
            [
                _num_token(value),
                str(status),
                _num_token(dl),
                sorted({normalise_code(c) for c in codes}),
            ]
            for value, status, dl, codes in candidates
        )
        return cls(sample_key, feature_id, _sha256(canon))
