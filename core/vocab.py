"""Vocabulary: immutable lookups over a loaded Config (spec §4.1; addendum A7, A13).

Lookups are pure. Usage is recorded only into an explicit `UsageTracker`, which the
orchestrator owns; `snapshot(used)` returns only the entries it recorded.
"""

from __future__ import annotations

import dataclasses
import difflib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from core.config import AnalyteRow, Config, ConversionRow, DateRuleRow, HeaderRuleRow, UnitRow
from core.ids import normalise_code, normalise_token

Analyte = AnalyteRow
Unit = UnitRow
Conversion = ConversionRow


class UnknownAnalyteError(KeyError):
    def __str__(self) -> str:
        return f"Unknown analyte {self.args[0]!r}: it is not in the analyte vocabulary."


class UnknownUnitError(KeyError):
    def __str__(self) -> str:
        return f"Unknown unit {self.args[0]!r}: it is not in the unit vocabulary."


@dataclass
class UsageTracker:
    """Mutable record of vocabulary entries actually referenced during a run."""

    analytes: set[str] = field(default_factory=set)
    units: set[str] = field(default_factory=set)
    conversions: set[tuple[str, str, str]] = field(default_factory=set)
    date_rules: bool = False
    header_rules: bool = False
    thresholds: set[str] = field(default_factory=set)
    tokens: set[str] = field(default_factory=set)
    flag_codes: set[str] = field(default_factory=set)

    def to_json(self) -> dict[str, Any]:
        return {
            "analytes": sorted(self.analytes),
            "units": sorted(self.units),
            "conversions": sorted([list(c) for c in self.conversions]),
            "date_rules": self.date_rules,
            "header_rules": self.header_rules,
            "thresholds": sorted(self.thresholds),
            "tokens": sorted(self.tokens),
            "flag_codes": sorted(self.flag_codes),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> UsageTracker:
        return cls(
            analytes=set(data["analytes"]),
            units=set(data["units"]),
            conversions={(a, b, c) for a, b, c in data["conversions"]},
            date_rules=bool(data["date_rules"]),
            header_rules=bool(data["header_rules"]),
            thresholds=set(data["thresholds"]),
            tokens=set(data["tokens"]),
            flag_codes=set(data["flag_codes"]),
        )

    @classmethod
    def union(cls, trackers: Iterable[UsageTracker]) -> UsageTracker:
        out = cls()
        for t in trackers:
            out.analytes |= t.analytes
            out.units |= t.units
            out.conversions |= t.conversions
            out.date_rules = out.date_rules or t.date_rules
            out.header_rules = out.header_rules or t.header_rules
            out.thresholds |= t.thresholds
            out.tokens |= t.tokens
            out.flag_codes |= t.flag_codes
        return out


@dataclass(frozen=True)
class Clash:
    token: str  # the normalised token that is already taken
    existing: str  # key of the existing analyte / unit holding it


@dataclass(frozen=True)
class NearMatch:
    token: str
    existing: str
    ratio: float


@dataclass(frozen=True)
class CheckResult:
    clashes: tuple[Clash, ...]
    near_matches: tuple[NearMatch, ...]

    @property
    def blocked(self) -> bool:
        return bool(self.clashes)


def _tok(s: str) -> str:
    return normalise_token(s)


def _analyte_tokens(a: AnalyteRow) -> set[str]:
    return {t for t in (_tok(a.key), _tok(a.name), *(_tok(x) for x in a.aliases)) if t}


@dataclass(frozen=True)
class Vocabulary:
    """Defaults layered with project overrides, resolved once per run."""

    config: Config
    _tracker: UsageTracker | None = field(default=None, repr=False, compare=False)

    def tracking(self, tracker: UsageTracker) -> Vocabulary:
        """View whose lookups record into `tracker` when no explicit `used=` is given."""
        return dataclasses.replace(self, _tracker=tracker)

    def _used(self, used: UsageTracker | None) -> UsageTracker | None:
        return used if used is not None else self._tracker

    # -- core lookups -------------------------------------------------------------

    def analyte(self, key: str, *, used: UsageTracker | None = None) -> Analyte:
        used = self._used(used)
        nkey = _tok(key)
        for a in self.config.analytes:
            if _tok(a.key) == nkey:
                if used is not None:
                    used.analytes.add(_tok(a.key))
                return a
        raise UnknownAnalyteError(key)

    def unit(self, key: str, *, used: UsageTracker | None = None) -> Unit:
        used = self._used(used)
        nkey = _tok(key)
        for u in self.config.units:
            if _tok(u.unit) == nkey:
                if used is not None:
                    used.units.add(_tok(u.unit))
                return u
        raise UnknownUnitError(key)

    def conversion(
        self,
        frm: str,
        to: str,
        analyte: str | None = None,
        *,
        used: UsageTracker | None = None,
    ) -> Conversion | None:
        """Analyte-specific row, else generic row, else None. Never guessed (§6.4)."""
        used = self._used(used)
        wanted = [(_tok(frm), _tok(to), _tok(analyte))] if analyte else []
        wanted.append((_tok(frm), _tok(to), ""))
        by_key = {
            (_tok(c.from_unit), _tok(c.to_unit), _tok(c.analyte or "")): c
            for c in self.config.conversions
        }
        for k in wanted:
            if k in by_key:
                if used is not None:
                    used.conversions.add(k)
                return by_key[k]
        return None

    # -- other configuration reads ---------------------------------------------------

    def date_rules(self, *, used: UsageTracker | None = None) -> tuple[DateRuleRow, ...]:
        used = self._used(used)
        if used is not None:
            used.date_rules = True
        return self.config.date_rules

    def header_rules(self, *, used: UsageTracker | None = None) -> tuple[HeaderRuleRow, ...]:
        used = self._used(used)
        if used is not None:
            used.header_rules = True
        return self.config.header_rules

    def threshold(self, name: str, *, used: UsageTracker | None = None) -> Any:
        if name not in type(self.config.qa_thresholds).model_fields:
            raise KeyError(f"Unknown QA threshold {name!r}.")
        used = self._used(used)
        if used is not None:
            used.thresholds.add(name)
        value = getattr(self.config.qa_thresholds, name)
        return value.model_dump() if hasattr(value, "model_dump") else value

    def token(self, name: str, *, used: UsageTracker | None = None) -> Any:
        if name not in type(self.config.tokens).model_fields:
            raise KeyError(f"Unknown token setting {name!r}.")
        used = self._used(used)
        if used is not None:
            used.tokens.add(name)
        return getattr(self.config.tokens, name)

    def flag_action(self, code: str, *, used: UsageTracker | None = None) -> str | None:
        used = self._used(used)
        norm = normalise_code(code)
        action = self.config.flag_map.get(norm)
        if action is not None and used is not None:
            used.flag_codes.add(norm)
        return action

    # -- custom-vocabulary validation helpers ----------------------------------------

    def _near(self, token: str, pool: dict[str, str]) -> list[NearMatch]:
        ratio = self.config.tokens.alias_near_match_ratio
        found = []
        for other, owner in pool.items():
            if other == token:
                continue
            r = difflib.SequenceMatcher(None, token, other).ratio()
            if r >= ratio:
                found.append(NearMatch(token, owner, r))
        return found

    def check_new_analyte(self, key: str, name: str, aliases: Sequence[str] = ()) -> CheckResult:
        """Blocking clashes with existing keys/names/aliases, plus 'did you mean' near matches."""
        pool: dict[str, str] = {}
        for a in self.config.analytes:
            for t in _analyte_tokens(a):
                pool[t] = a.key
        candidates = [t for t in (_tok(key), _tok(name), *(_tok(a) for a in aliases)) if t]
        clashes = tuple(Clash(t, pool[t]) for t in dict.fromkeys(candidates) if t in pool)
        clashed = {c.token for c in clashes}
        near: list[NearMatch] = []
        for t in dict.fromkeys(candidates):
            if t not in clashed:
                near.extend(self._near(t, pool))
        near.sort(key=lambda n: (-n.ratio, n.token, n.existing))
        return CheckResult(clashes, tuple(near))

    def check_new_unit(self, unit: str) -> CheckResult:
        pool = {_tok(u.unit): u.unit for u in self.config.units}
        t = _tok(unit)
        if t in pool:
            return CheckResult((Clash(t, pool[t]),), ())
        near = sorted(self._near(t, pool), key=lambda n: (-n.ratio, n.existing))
        return CheckResult((), tuple(near))

    # -- snapshot (recipe reproducibility only; not part of any fingerprint) ----------

    def snapshot(self, used: UsageTracker) -> dict[str, Any]:
        cfg = self.config
        analytes = sorted(
            (a for a in cfg.analytes if _tok(a.key) in used.analytes), key=lambda a: _tok(a.key)
        )
        units = sorted(
            (u for u in cfg.units if _tok(u.unit) in used.units), key=lambda u: _tok(u.unit)
        )
        conversions = sorted(
            (
                c
                for c in cfg.conversions
                if (_tok(c.from_unit), _tok(c.to_unit), _tok(c.analyte or "")) in used.conversions
            ),
            key=lambda c: (_tok(c.from_unit), _tok(c.to_unit), _tok(c.analyte or "")),
        )
        return {
            "analytes": [a.model_dump(mode="json") for a in analytes],
            "units": [u.model_dump(mode="json") for u in units],
            "conversions": [c.model_dump(mode="json") for c in conversions],
            "date_rules": [r.model_dump(mode="json") for r in cfg.date_rules]
            if used.date_rules
            else [],
            "header_rules": [r.model_dump(mode="json") for r in cfg.header_rules]
            if used.header_rules
            else [],
            "tokens": {n: _jsonable(getattr(cfg.tokens, n)) for n in sorted(used.tokens)},
            "thresholds": {
                n: _jsonable(getattr(cfg.qa_thresholds, n)) for n in sorted(used.thresholds)
            },
            "flag_map": {c: cfg.flag_map[c] for c in sorted(used.flag_codes)},
            "files": {n: list(h) for n, h in sorted(cfg.file_hashes.items())},
        }


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    return value
