"""Config loader: additive layers of CSV/JSON files (spec §7; addendum A9, A13).

Layers are read in order (shipped defaults first, then project). Conflict rules:
vocabulary CSVs block clashing rows (identical rows are a logged no-op); rule CSVs are
concatenated and stably sorted by `order`; JSON files deep-merge (scalars: later layer wins,
lists: union). Every file is read once and hashed from the same bytes.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from core.ids import normalise_code, normalise_token
from core.pipeline import FileLockedError, ReportItem, resolve_config_files
from core.profiles import WATER

CONFIG_FILES: tuple[str, ...] = (
    "known_analytes.csv",
    "known_units.csv",
    "conversions.csv",
    "flag_map.json",
    "date_rules.csv",
    "header_rules.csv",
    "tokens.json",
    "qa_thresholds.json",
)
OPTIONAL_FILES: frozenset[str] = frozenset({"flag_map.json"})
PLACEHOLDER_MARKER = "PLACEHOLDER"
PLACEHOLDER_WARNING = "using placeholder configuration"
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "defaults"
SU_UNIT = "S.U."

COLUMN_ROLES: tuple[str, ...] = (
    "metadata",
    "analyte",
    "result",
    "unit",
    "fraction",
    "qualifier",
    "detect_flag",
    "dl_mdl",
    "dl_rl",
    "sample_type",
    "parent_sample_id",
    "ignore",
)
FlagAction = Literal["Ignore", "DL", "Bad"]
T = TypeVar("T", bound=BaseModel)


class ConfigError(ValueError):
    """A config file is unreadable or invalid; the message names the file, row or key."""


# ------------------------------------------------------------------ row models


def _blank_to_none(v: Any) -> Any:
    return None if isinstance(v, str) and v.strip() == "" else v


def _split_aliases(v: Any) -> Any:
    if isinstance(v, str):
        return tuple(p.strip() for p in v.split(";") if p.strip())
    return v


def _parse_bool(v: Any) -> Any:
    if isinstance(v, str):
        low = v.strip().lower()
        if low in ("true", "false"):
            return low == "true"
        raise ValueError("must be 'true' or 'false'")
    return v


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AnalyteRow(_Row):
    name: str = Field(min_length=1)
    key: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    analyte_class: str = ""
    symbol: str = ""
    plot_label: str = ""
    filename_key: str = ""
    transform: Literal["log", "none"]
    allow_negative: bool
    molar_mass: float | None = None
    charge: int | None = None
    source: str = "shipped"

    @field_validator("aliases", mode="before")
    @classmethod
    def _aliases(cls, v: Any) -> Any:
        return _split_aliases(v)

    @field_validator("allow_negative", mode="before")
    @classmethod
    def _bool(cls, v: Any) -> Any:
        return _parse_bool(v)

    @field_validator("molar_mass", "charge", mode="before")
    @classmethod
    def _opt(cls, v: Any) -> Any:
        return _blank_to_none(v)

    @model_validator(mode="after")
    def _defaults(self) -> AnalyteRow:
        for attr, fallback in (
            ("symbol", self.name),
            ("plot_label", self.name),
            ("filename_key", self.key),
        ):
            if not getattr(self, attr):
                object.__setattr__(self, attr, fallback)
        return self


class UnitRow(_Row):
    unit: str = Field(min_length=1)
    unit_class: str = Field(min_length=1)
    applies_to: Literal["water", "physical", "any"]
    column_suffix: str = ""
    source: str = "shipped"


class ConversionRow(_Row):
    from_unit: str = Field(min_length=1)
    to_unit: str = Field(min_length=1)
    factor: float
    offset: float = 0.0
    analyte: str | None = None
    source: str = "shipped"

    @field_validator("analyte", mode="before")
    @classmethod
    def _analyte(cls, v: Any) -> Any:
        return _blank_to_none(v)

    @field_validator("offset", mode="before")
    @classmethod
    def _offset(cls, v: Any) -> Any:
        return 0.0 if _blank_to_none(v) is None else v


def _compile(pattern: str) -> str:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"not a valid regular expression: {exc}") from exc
    return pattern


class DateRuleRow(_Row):
    order: int
    source_pattern: str | None = None
    regex: str
    format: str = Field(min_length=1)
    source: str = "shipped"

    @field_validator("source_pattern", mode="before")
    @classmethod
    def _sp(cls, v: Any) -> Any:
        return _blank_to_none(v)

    @field_validator("regex")
    @classmethod
    def _rx(cls, v: str) -> str:
        return _compile(v)


class HeaderRuleRow(_Row):
    order: int
    regex: str
    source: str = "shipped"

    @field_validator("regex")
    @classmethod
    def _rx(cls, v: str) -> str:
        return _compile(v)


class _Doc(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class HeaderCleanup(_Doc):
    trim: bool
    collapse_whitespace: bool
    case: Literal["none", "lower", "upper"]


class NumericLocale(_Doc):
    decimal_separator: Literal[".", ","]
    thousands_separator: str = Field(max_length=1)


class Tokens(_Doc):
    no_result_tokens: tuple[str, ...]
    metadata_keywords: tuple[str, ...]
    column_role_keywords: Mapping[str, tuple[str, ...]]
    sample_type_aliases: Mapping[str, tuple[str, ...]]
    header_cleanup: HeaderCleanup
    numeric_locale: NumericLocale
    name_template: str = Field(min_length=1)
    alias_near_match_ratio: float = Field(gt=0.0, le=1.0)
    units_row_labels: tuple[str, ...]
    dl_row_labels: tuple[str, ...]

    @field_validator("column_role_keywords")
    @classmethod
    def _roles(cls, v: Mapping[str, tuple[str, ...]]) -> Mapping[str, tuple[str, ...]]:
        bad = sorted(set(v) - set(COLUMN_ROLES))
        if bad:
            raise ValueError(f"unknown column role(s) {bad}; allowed: {list(COLUMN_ROLES)}")
        return v

    @field_validator("sample_type_aliases")
    @classmethod
    def _types(cls, v: Mapping[str, tuple[str, ...]]) -> Mapping[str, tuple[str, ...]]:
        bad = sorted(set(v) - set(WATER.sample_types))
        if bad:
            raise ValueError(
                f"unknown canonical sample type(s) {bad}; allowed: {list(WATER.sample_types)}"
            )
        return v


class Range(_Doc):
    min: float
    max: float

    @model_validator(mode="after")
    def _order(self) -> Range:
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


class QaThresholds(_Doc):
    nd_warn_pct: float
    dominant_warn_pct: float
    ion_balance_tol: float
    dt_tol: float
    tds_ec_range: Range
    high_dl_percentile: float
    min_obs_per_cell: int = Field(ge=1)


class _FlagMapDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: dict[str, FlagAction]


# ------------------------------------------------------------------ result container


@dataclass(frozen=True)
class Config:
    analytes: tuple[AnalyteRow, ...]
    units: tuple[UnitRow, ...]
    conversions: tuple[ConversionRow, ...]
    flag_map: Mapping[str, FlagAction]
    date_rules: tuple[DateRuleRow, ...]
    header_rules: tuple[HeaderRuleRow, ...]
    tokens: Tokens
    qa_thresholds: QaThresholds
    file_hashes: Mapping[str, tuple[str, ...]]  # per file name: SHA-256 per resolved layer file
    layers: tuple[Path, ...]
    report: tuple[ReportItem, ...]
    placeholder: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "flag_map", MappingProxyType(dict(self.flag_map)))
        object.__setattr__(self, "file_hashes", MappingProxyType(dict(self.file_hashes)))


# ------------------------------------------------------------------ reading


def _read(path: Path) -> tuple[str, str]:
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except PermissionError as exc:
        raise FileLockedError(
            f"Cannot read '{path.name}'. If it is open in Excel, close it and try again."
        ) from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path}: not valid UTF-8 text ({exc.reason}).") from exc
    return text, hashlib.sha256(raw).hexdigest()


def _loc(err: Mapping[str, Any]) -> str:
    return ".".join(str(p) for p in err["loc"])


def _validation_message(path: Path, exc: ValidationError, prefix: str = "") -> str:
    parts = []
    for err in exc.errors():
        loc = _loc(err)
        if err["type"] == "extra_forbidden":
            parts.append(f"unknown key '{loc}'")
        else:
            parts.append(f"key '{loc}': {err['msg']}" if loc else str(err["msg"]))
    return f"{path}{prefix}: " + "; ".join(parts)


@dataclass(frozen=True)
class _CsvSpec:
    model: type[BaseModel]
    required: tuple[str, ...]
    optional: tuple[str, ...] = ("source",)


_SPECS: dict[str, _CsvSpec] = {
    "known_analytes.csv": _CsvSpec(
        AnalyteRow,
        (
            "name",
            "key",
            "aliases",
            "analyte_class",
            "symbol",
            "plot_label",
            "filename_key",
            "transform",
            "allow_negative",
            "molar_mass",
            "charge",
        ),
    ),
    "known_units.csv": _CsvSpec(UnitRow, ("unit", "unit_class", "applies_to", "column_suffix")),
    "conversions.csv": _CsvSpec(
        ConversionRow, ("from_unit", "to_unit", "factor", "offset", "analyte")
    ),
    "date_rules.csv": _CsvSpec(DateRuleRow, ("order", "source_pattern", "regex", "format")),
    "header_rules.csv": _CsvSpec(HeaderRuleRow, ("order", "regex")),
}


@dataclass(frozen=True)
class _Parsed:
    row: Any
    line: int
    path: Path


def _parse_csv(path: Path, text: str, name: str, source: str) -> list[_Parsed]:
    spec = _SPECS[name]
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in spec.required if c not in header]
    unexpected = [c for c in header if c not in spec.required + spec.optional]
    if missing or unexpected:
        raise ConfigError(
            f"{path}: columns do not match the expected schema"
            f" (missing: {missing or 'none'}; unexpected: {unexpected or 'none'})."
        )
    reader.fieldnames = header
    out: list[_Parsed] = []
    for rec in reader:
        line = reader.line_num
        if None in rec or any(v is None for v in rec.values()):
            raise ConfigError(f"{path} line {line}: wrong number of fields for the header.")
        cleaned = {k: (v or "").strip() for k, v in rec.items()}
        if not cleaned.get("source"):
            cleaned["source"] = source
        try:
            row = spec.model.model_validate(cleaned)
        except ValidationError as exc:
            raise ConfigError(_validation_message(path, exc, f" line {line}")) from exc
        out.append(_Parsed(row, line, path))
    return out


def _parse_json(path: Path, text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: not valid JSON ({exc}).") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a JSON object at the top level.")
    return data


# ------------------------------------------------------------------ merging


def _kind(v: Any) -> str:
    if isinstance(v, dict):
        return "object"
    if isinstance(v, list):
        return "list"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int | float):
        return "number"
    if isinstance(v, str):
        return "text"
    return "null"


def _merge_json(
    base: dict[str, Any],
    over: dict[str, Any],
    *,
    path: Path,
    file: str,
    report: list[ReportItem],
    trail: str = "",
) -> dict[str, Any]:
    out = dict(base)
    for key, new in over.items():
        here = f"{trail}.{key}" if trail else key
        if key not in out:
            out[key] = new
            continue
        old = out[key]
        if _kind(old) != _kind(new):
            raise ConfigError(
                f"{path}: key '{here}' is {_kind(new)} here but {_kind(old)} in a lower layer."
            )
        if isinstance(old, dict):
            out[key] = _merge_json(old, new, path=path, file=file, report=report, trail=here)
        elif isinstance(old, list):
            added = [x for x in new if x not in old]
            out[key] = old + added
            if added:
                report.append(
                    ReportItem(
                        "config_list_extended",
                        f"{file}:{here}",
                        len(added),
                        {"added": added, "layer": str(path)},
                    )
                )
        elif old != new:
            out[key] = new
            report.append(
                ReportItem(
                    "config_value_overridden",
                    f"{file}:{here}",
                    1,
                    {"before": old, "after": new, "layer": str(path)},
                )
            )
    return out


def _normalise_flag_keys(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for code, action in data.items():
        norm = normalise_code(code)
        if norm in out:
            raise ConfigError(f"{path}: qualifier code '{code}' duplicates another code '{norm}'.")
        out[norm] = action
    return out


def _validate_doc(model: type[T], data: dict[str, Any], path: Path) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_validation_message(path, exc)) from exc


def _load_json(
    name: str,
    files: Sequence[Path],
    texts: dict[Path, str],
    model: type[T],
    report: list[ReportItem],
    prepare: Callable[[Path, dict[str, Any]], dict[str, Any]] | None = None,
    wrap: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], T | None]:
    acc: dict[str, Any] = {}
    doc: T | None = None
    for i, path in enumerate(files):
        data = _parse_json(path, texts[path])
        if prepare:
            data = prepare(path, data)
        acc = data if i == 0 else _merge_json(acc, data, path=path, file=name, report=report)
        doc = _validate_doc(model, wrap(acc) if wrap else acc, path)
    return acc, doc


def _tokens(p: str) -> str:
    return normalise_token(p)


def _analyte_sig(r: AnalyteRow) -> tuple[Any, ...]:
    return (
        _tokens(r.key),
        _tokens(r.name),
        tuple(sorted({_tokens(a) for a in r.aliases})),
        _tokens(r.analyte_class),
        _tokens(r.symbol),
        _tokens(r.plot_label),
        _tokens(r.filename_key),
        r.transform,
        r.allow_negative,
        r.molar_mass,
        r.charge,
    )


def _merge_analytes(
    rows: list[_Parsed], name: str, report: list[ReportItem]
) -> tuple[AnalyteRow, ...]:
    kept: dict[str, tuple[AnalyteRow, int, Path]] = {}  # normalised key -> row
    owner: dict[str, str] = {}  # token -> analyte key that owns it
    for p in rows:
        r: AnalyteRow = p.row
        nkey = _tokens(r.key)
        toks = {nkey, _tokens(r.name), *(_tokens(a) for a in r.aliases)} - {""}
        if nkey in kept:
            prev, _, prev_path = kept[nkey]
            if prev_path != p.path and _analyte_sig(prev) == _analyte_sig(r):
                report.append(
                    ReportItem(
                        "config_row_ignored_identical",
                        f"{name}:{prev.key}",
                        1,
                        {"layer": str(p.path), "line": p.line},
                    )
                )
                continue
        for tok in sorted(toks):
            if tok in owner:
                raise ConfigError(
                    f"{p.path} line {p.line}: analyte '{r.key}' reuses '{tok}', "
                    f"already used by analyte '{owner[tok]}'."
                )
        kept[nkey] = (r, p.line, p.path)
        for tok in toks:
            owner[tok] = r.key
    return tuple(r for r, _, _ in kept.values())


def _merge_keyed(
    rows: list[_Parsed],
    name: str,
    key: Callable[[Any], Any],
    sig: Callable[[Any], Any],
    describe: Callable[[Any], str],
    report: list[ReportItem],
) -> tuple[Any, ...]:
    kept: dict[Any, tuple[Any, Path]] = {}
    for p in rows:
        k = key(p.row)
        if k in kept:
            prev, prev_path = kept[k]
            if prev_path != p.path and sig(prev) == sig(p.row):
                report.append(
                    ReportItem(
                        "config_row_ignored_identical",
                        f"{name}:{describe(prev)}",
                        1,
                        {"layer": str(p.path), "line": p.line},
                    )
                )
                continue
            where = "twice in this file" if prev_path == p.path else f"in {prev_path.name}"
            raise ConfigError(
                f"{p.path} line {p.line}: {describe(p.row)} is already defined {where}"
                " and the rows differ or are duplicated."
            )
        kept[k] = (p.row, p.path)
    return tuple(r for r, _ in kept.values())


def _merge_rules(rows: list[_Parsed]) -> tuple[Any, ...]:
    indexed = sorted(enumerate(rows), key=lambda t: (t[1].row.order, t[0]))
    return tuple(p.row for _, p in indexed)


# ------------------------------------------------------------------ public API


def config_layers(defaults_dir: Path, project_dir: Path | None = None) -> list[Path]:
    """Layer directories in order: shipped defaults, then `<project>/config/` if given."""
    layers = [Path(defaults_dir)]
    if project_dir is not None:
        layers.append(Path(project_dir) / "config")
    return layers


def load_config(layers: Sequence[Path]) -> Config:
    layer_list = tuple(Path(p) for p in layers)
    report: list[ReportItem] = []
    resolved = resolve_config_files(CONFIG_FILES, layer_list)
    texts: dict[Path, str] = {}
    hashes: dict[str, tuple[str, ...]] = {}
    for name, files in resolved.items():
        if not files and name not in OPTIONAL_FILES:
            raise ConfigError(
                f"Required config file '{name}' not found in any layer: "
                f"{[str(p) for p in layer_list]}."
            )
        digests = []
        for path in files:
            texts[path], digest = _read(path)
            digests.append(digest)
        hashes[name] = tuple(digests)

    def layer_source(path: Path) -> str:
        return "shipped" if path.parent == layer_list[0] else "user"

    def csv_rows(name: str) -> list[_Parsed]:
        out: list[_Parsed] = []
        for path in resolved[name]:
            out.extend(_parse_csv(path, texts[path], name, layer_source(path)))
        return out

    analytes = _merge_analytes(csv_rows("known_analytes.csv"), "known_analytes.csv", report)
    units = _merge_keyed(
        csv_rows("known_units.csv"),
        "known_units.csv",
        key=lambda r: _tokens(r.unit),
        sig=lambda r: (
            _tokens(r.unit),
            _tokens(r.unit_class),
            r.applies_to,
            _tokens(r.column_suffix),
        ),
        describe=lambda r: f"unit '{r.unit}'",
        report=report,
    )
    conversions = _merge_keyed(
        csv_rows("conversions.csv"),
        "conversions.csv",
        key=lambda r: (_tokens(r.from_unit), _tokens(r.to_unit), _tokens(r.analyte or "")),
        sig=lambda r: (r.factor, r.offset),
        describe=lambda r: (
            f"conversion '{r.from_unit}' -> '{r.to_unit}'"
            + (f" for '{r.analyte}'" if r.analyte else "")
        ),
        report=report,
    )
    date_rules = _merge_rules(csv_rows("date_rules.csv"))
    header_rules = _merge_rules(csv_rows("header_rules.csv"))

    _, flag_doc = _load_json(
        "flag_map.json",
        resolved["flag_map.json"],
        texts,
        _FlagMapDoc,
        report,
        prepare=_normalise_flag_keys,
        wrap=lambda d: {"root": d},
    )
    flag_map: dict[str, FlagAction] = dict(flag_doc.root) if flag_doc else {}
    _, tokens = _load_json("tokens.json", resolved["tokens.json"], texts, Tokens, report)
    _, qa = _load_json(
        "qa_thresholds.json", resolved["qa_thresholds.json"], texts, QaThresholds, report
    )
    assert tokens is not None and qa is not None

    _cross_check(analytes, units, conversions, layer_list)

    placeholder = (layer_list[0] / PLACEHOLDER_MARKER).is_file()
    if placeholder:
        report.append(
            ReportItem(
                "placeholder_config",
                str(layer_list[0]),
                1,
                {"marker": PLACEHOLDER_MARKER},
                warning=PLACEHOLDER_WARNING,
            )
        )
    return Config(
        analytes=analytes,
        units=units,
        conversions=conversions,
        flag_map=flag_map,
        date_rules=date_rules,
        header_rules=header_rules,
        tokens=tokens,
        qa_thresholds=qa,
        file_hashes=hashes,
        layers=layer_list,
        report=tuple(report),
        placeholder=placeholder,
    )


def _cross_check(
    analytes: Sequence[AnalyteRow],
    units: Sequence[UnitRow],
    conversions: Sequence[ConversionRow],
    layers: Sequence[Path],
) -> None:
    unit_keys = {_tokens(u.unit) for u in units}
    analyte_keys = {_tokens(a.key) for a in analytes}
    su = [u for u in units if _tokens(u.unit) == _tokens(SU_UNIT)]
    if not su:
        raise ConfigError(f"known_units.csv must define '{SU_UNIT}' in its own unit class.")
    cls = _tokens(su[0].unit_class)
    others = [u.unit for u in units if _tokens(u.unit_class) == cls and u is not su[0]]
    if others:
        raise ConfigError(
            f"known_units.csv: '{SU_UNIT}' must be in its own unit class; it shares "
            f"'{su[0].unit_class}' with {others}."
        )
    for c in conversions:
        for u in (c.from_unit, c.to_unit):
            if _tokens(u) not in unit_keys:
                raise ConfigError(f"conversions.csv: conversion uses unknown unit '{u}'.")
        if c.analyte is not None and _tokens(c.analyte) not in analyte_keys:
            raise ConfigError(f"conversions.csv: conversion uses unknown analyte '{c.analyte}'.")


def reload_config(prev: Config) -> tuple[Config, tuple[ReportItem, ...]]:
    """Re-read the layers; report each file whose content hashes changed (before/after)."""
    new = load_config(prev.layers)
    changes = tuple(
        ReportItem(
            "config_reload",
            name,
            1,
            {"before": list(prev.file_hashes.get(name, ())), "after": list(new.file_hashes[name])},
        )
        for name in CONFIG_FILES
        if prev.file_hashes.get(name, ()) != new.file_hashes[name]
    )
    return new, changes
