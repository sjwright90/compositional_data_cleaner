"""Pipeline contract types: report items, stage results and the Stage protocol."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from pathlib import Path, PurePath
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Protocol, TypeVar

import numpy as np
import pandas as pd
from pydantic import BaseModel

if TYPE_CHECKING:
    from core.vocab import Vocabulary

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
    blocking: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))
        if self.blocking and not self.warning:
            raise ValueError(
                f"Blocking report item '{self.action}' on '{self.target}' needs a warning text."
            )


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


@dataclass(frozen=True)
class SyncResult:
    """`params` with entries added for new keys; `orphans` are gate-path prefixes (e.g.
    `entries[old]`) of decided entries whose key is no longer present."""

    params: BaseModel
    orphans: tuple[str, ...] = ()


class SupportsSuggest(Protocol[In, P]):
    """Optional. Pure and deterministic; returns stage-relative gate path -> suggested value."""

    def suggest(self, inputs: In, params: P, vocab: Vocabulary) -> Mapping[str, Any]: ...


class SupportsSyncParams(Protocol[In, P]):
    """Optional. Pure, idempotent. Adds an entry (unresolved gates) per new key; keeps decided
    entries unchanged; drops undecided entries whose key is gone; lists decided ones as orphans."""

    def sync_params(self, inputs: In, params: P, vocab: Vocabulary) -> SyncResult: ...


# ---- fingerprints, config hashing and staleness (spec §4.2; addendum A7) ----

MISSING_CONFIG_MARKER = "MISSING"
_HASH_CHUNK = 1 << 20


class Status(StrEnum):
    DONE = "done"
    STALE = "stale"
    BLOCKED = "blocked"


class FileLockedError(PermissionError):
    """A file could not be read because another program (typically Excel) holds it."""


class StageMeta(Protocol):
    """The attributes of a stage that fingerprinting reads."""

    name: str
    version: str
    depends_on: tuple[str, ...]
    config_files: tuple[str, ...]


def _normalise(obj: Any) -> Any:
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, Enum):
        return _normalise(obj.value)
    if obj is None or isinstance(obj, bool | str):
        return obj
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(
                f"Cannot fingerprint a parameter containing NaN or infinite numbers (found {obj})."
                " Replace the value or leave it unset."
            )
        return 0.0 if obj == 0.0 else float(obj)
    if isinstance(obj, PurePath):
        return obj.as_posix()
    if isinstance(obj, BaseModel):
        return _normalise(obj.model_dump(mode="python"))
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _normalise({f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)})
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            raw_key = key.value if isinstance(key, Enum) else key
            if not isinstance(raw_key, str):
                raise TypeError(f"Mapping keys must be strings, got {type(key).__name__}: {key!r}")
            out[raw_key] = _normalise(value)
        return out
    if isinstance(obj, set | frozenset):
        items = [_normalise(x) for x in obj]
        return sorted(items, key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(obj, list | tuple):
        return [_normalise(x) for x in obj]
    raise TypeError(f"Cannot render {type(obj).__name__} as canonical JSON")


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, compact, ASCII; floats via repr, -0.0 -> 0.0."""
    return json.dumps(
        _normalise(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while chunk := fh.read(_HASH_CHUNK):
                digest.update(chunk)
    except PermissionError as exc:
        raise FileLockedError(
            f"Cannot read '{Path(path).name}'. If it is open in Excel, close it and try again."
        ) from exc
    return digest.hexdigest()


def resolve_config_files(
    names: Iterable[str], layers: Sequence[Path]
) -> dict[str, tuple[Path, ...]]:
    """Files the config loader reads for each name, in layer order (defaults first).

    Layers are additive (spec §7: project files are layered on top of the defaults, and
    custom rows are written to project files), so every layer holding the file is read.
    An empty tuple means no layer has the file. The T04 loader must call this function.
    """
    return {
        name: tuple(p for layer in layers if (p := Path(layer) / name).is_file()) for name in names
    }


def config_hash(stage: StageMeta, layers: Sequence[Path]) -> str:
    """SHA-256 over the resolved files of `stage.config_files`: (name, content hashes)."""
    resolved = resolve_config_files(sorted(set(stage.config_files)), layers)
    entries = [
        [name, [file_sha256(p) for p in paths] if paths else MISSING_CONFIG_MARKER]
        for name, paths in resolved.items()
    ]
    return _sha256_text(canonical_json(entries))


def fingerprint(
    stage: StageMeta,
    params: Any,
    dep_fingerprints: Mapping[str, str],
    config_hash: str,
) -> str:
    """SHA-256 of stage name, version, canonical params, dependency fingerprints, config hash."""
    expected = set(stage.depends_on)
    given = set(dep_fingerprints)
    if expected != given:
        raise ValueError(
            f"dependency fingerprints for stage {stage.name!r} do not match depends_on: "
            f"missing {sorted(expected - given)}, unexpected {sorted(given - expected)}"
        )
    payload = {
        "name": stage.name,
        "version": stage.version,
        "params": params,
        "deps": [[d, dep_fingerprints[d]] for d in sorted(given)],
        "config": config_hash,
    }
    return _sha256_text(canonical_json(payload))


def compute_staleness(
    stored: Mapping[str, str],
    recomputed: Mapping[str, str],
    depends_on: Mapping[str, Sequence[str]],
) -> dict[str, Status]:
    """Per-stage status from stored vs recomputed fingerprints; never set by hand.

    BLOCKED: a direct dependency has no stored fingerprint or is itself BLOCKED.
    STALE: a dependency is STALE, or the stage has no stored fingerprint, or it differs.
    DONE: stored fingerprint equals the recomputed one and all dependencies are DONE.
    """
    if set(recomputed) != set(depends_on):
        raise ValueError(
            "recomputed fingerprints must cover exactly the stages in depends_on: "
            f"missing {sorted(set(depends_on) - set(recomputed))}, "
            f"unexpected {sorted(set(recomputed) - set(depends_on))}"
        )
    for stage, deps in depends_on.items():
        unknown = sorted(set(deps) - set(depends_on))
        if unknown:
            raise ValueError(f"stage {stage!r} depends on unknown stage(s): {unknown}")

    remaining = {s: set(d) for s, d in depends_on.items()}
    order: list[str] = []
    while remaining:
        ready = sorted(s for s, d in remaining.items() if d <= set(order))
        if not ready:
            raise ValueError(f"dependency cycle among stages: {sorted(remaining)}")
        order.extend(ready)
        for s in ready:
            del remaining[s]

    result: dict[str, Status] = {}
    for stage in order:
        deps = depends_on[stage]
        if any(result[d] is Status.BLOCKED or d not in stored for d in deps):
            result[stage] = Status.BLOCKED
        elif any(result[d] is Status.STALE for d in deps) or stored.get(stage) != recomputed[stage]:
            result[stage] = Status.STALE
        else:
            result[stage] = Status.DONE
    return result
