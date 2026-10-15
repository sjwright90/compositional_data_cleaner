"""Recipe tree and gate fields (spec §8; addendum A3, A9)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import types
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Generic, Literal, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.migrations import CURRENT, Migration, migrate
from core.pipeline import ReportItem, canonical_json, file_sha256

logger = logging.getLogger(__name__)

T = TypeVar("T")
M = TypeVar("M", bound=BaseModel)

_GATE_DEFAULT_KEY = "gate_default"
_GATE_SUGGESTED_KEY = "gate_suggested"


# ---- gates ----


class AcceptDefault(BaseModel, Generic[T]):
    """`{"accept_default": true, "value": <resolved default>}` (refines A3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accept_default: Literal[True]
    value: T


# A gate field is unresolved (None), an explicit decision (T) or an accepted default.
Gate = Union[T, AcceptDefault[T], None]  # noqa: UP007  (TypeVar union must stay subscriptable)


def gate_field(default: Any, *, suggested: bool = False) -> Any:
    """Declare a gate on a stage's `Params`; `default` is the value accepting it records.

    With `suggested=True` the value comes only from `Stage.suggest`; `default` is not used to
    resolve it (a missing suggestion leaves the gate unresolved).
    """
    return Field(
        default=None,
        json_schema_extra={_GATE_DEFAULT_KEY: default, _GATE_SUGGESTED_KEY: suggested},
    )


@dataclass(frozen=True)
class GateSpec:
    default: Any
    suggested: bool = False


def _is_gate(annotation: Any) -> bool:
    if get_origin(annotation) not in (Union, types.UnionType):
        return False
    return any(isinstance(a, type) and issubclass(a, AcceptDefault) for a in get_args(annotation))


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def _spec(info: Any, path: str) -> GateSpec:
    extra = info.json_schema_extra
    if not isinstance(extra, dict) or _GATE_DEFAULT_KEY not in extra:
        raise TypeError(f"Gate field '{path}' must be declared with gate_field(default).")
    return GateSpec(extra[_GATE_DEFAULT_KEY], bool(extra.get(_GATE_SUGGESTED_KEY, False)))


def gate_fields(params_cls: type[BaseModel], prefix: str = "") -> dict[str, GateSpec]:
    """Dotted field path -> spec, for every gate on a Params model (recursing into sub-models).

    Class level: sees flat fields and single sub-models only; use `gate_paths` on an instance
    to also find gates inside lists and dicts of sub-models.
    """
    out: dict[str, GateSpec] = {}
    for name, info in params_cls.model_fields.items():
        path = f"{prefix}{name}"
        if _is_gate(info.annotation):
            out[path] = _spec(info, path)
            continue
        nested = _nested_model(info.annotation)
        if nested is not None:
            out.update(gate_fields(nested, f"{path}."))
    return out


# ---- indexed paths: `a.b`, `sheets[2].layout`, `maps[key].unit` ----


def _esc(key: str) -> str:
    return key.replace("\\", "\\\\").replace("]", "\\]")


_TOKEN = re.compile(r"\.?([A-Za-z_]\w*)|\[((?:\\.|[^\]\\])*)\]")


def _tokens(path: str) -> list[tuple[str, bool]]:
    """`(name or key, is_index)` per path segment."""
    out: list[tuple[str, bool]] = []
    pos = 0
    while pos < len(path):
        m = _TOKEN.match(path, pos)
        if m is None or m.end() == pos:
            raise ValueError(f"Malformed gate path '{path}'.")
        if m.group(1) is not None:
            out.append((m.group(1), False))
        else:
            out.append((re.sub(r"\\(.)", r"\1", m.group(2)), True))
        pos = m.end()
    return out


def _index(container: Any, key: str) -> Any:
    if isinstance(container, Mapping):
        return container[key]
    return container[int(key)]


def get_path(params: BaseModel, path: str) -> Any:
    obj: Any = params
    for token, is_index in _tokens(path):
        obj = _index(obj, token) if is_index else getattr(obj, token)
    return obj


def _set(obj: Any, tokens: list[tuple[str, bool]], value: Any) -> Any:
    if not tokens:
        return value
    (token, is_index), rest = tokens[0], tokens[1:]
    if not is_index:
        return obj.model_copy(update={token: _set(getattr(obj, token), rest, value)})
    if isinstance(obj, Mapping):
        return {**obj, token: _set(obj[token], rest, value)}
    items = list(obj)
    items[int(token)] = _set(items[int(token)], rest, value)
    return tuple(items) if isinstance(obj, tuple) else items


def set_path(params: M, path: str, value: Any) -> M:
    """New params with the value at `path` replaced (immutable; no validation, like `_accept`)."""
    result: M = _set(params, _tokens(path), value)
    return result


def _walk(value: Any, path: str, out: dict[str, GateSpec]) -> None:
    if isinstance(value, BaseModel):
        out.update(gate_paths(value, f"{path}."))
    elif isinstance(value, Mapping):
        for key in sorted(value, key=str):
            _walk(value[key], f"{path}[{_esc(str(key))}]", out)
    elif isinstance(value, list | tuple):
        for i, item in enumerate(value):
            _walk(item, f"{path}[{i}]", out)


def gate_paths(params: BaseModel, prefix: str = "") -> dict[str, GateSpec]:
    """Path -> spec for every gate on an instance, including those inside lists and dicts of
    sub-models (`sheets[2].layout`; dict entries `maps[key].unit`)."""
    out: dict[str, GateSpec] = {}
    for name, info in type(params).model_fields.items():
        path = f"{prefix}{name}"
        if _is_gate(info.annotation):
            out[path] = _spec(info, path)
        else:
            _walk(getattr(params, name), path, out)
    return out


def _excluded(path: str, exclude: Sequence[str]) -> bool:
    return any(path == p or path.startswith((f"{p}.", f"{p}[")) for p in exclude)


def unresolved_gates(stage: str, params: BaseModel, *, exclude: Sequence[str] = ()) -> list[str]:
    """`<stage>.<path>` for every gate still unresolved, sorted. Paths under `exclude` (orphaned
    entries) are skipped."""
    return sorted(
        f"{stage}.{path}"
        for path in gate_paths(params)
        if not _excluded(path, exclude) and get_path(params, path) is None
    )


def accept_defaults(
    params: M,
    suggestions: Mapping[str, Any] | None = None,
    *,
    exclude: Sequence[str] = (),
) -> M:
    """New params with every resolvable unresolved gate set to an explicit accepted default.

    A `suggested` gate is resolved only from `suggestions`; without an entry it stays unresolved.
    """
    result: M = _accept_value(params, "", suggestions or {}, exclude)
    return result


def _accept_value(value: Any, path: str, suggestions: Mapping[str, Any], exclude: Any) -> Any:
    if isinstance(value, BaseModel):
        prefix = f"{path}." if path else ""
        update: dict[str, Any] = {}
        for name, info in type(value).model_fields.items():
            field_path = f"{prefix}{name}"
            if _excluded(field_path, exclude):
                continue
            current = getattr(value, name)
            if _is_gate(info.annotation):
                if current is not None:
                    continue
                spec = _spec(info, field_path)
                if spec.suggested:
                    if field_path not in suggestions:
                        continue
                    resolved = suggestions[field_path]
                else:
                    resolved = spec.default
                update[name] = AcceptDefault[Any](accept_default=True, value=resolved)
            else:
                update[name] = _accept_value(current, field_path, suggestions, exclude)
        return value.model_copy(update=update)
    if isinstance(value, Mapping):
        return {
            k: _accept_value(v, f"{path}[{_esc(str(k))}]", suggestions, exclude)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_accept_value(v, f"{path}[{i}]", suggestions, exclude) for i, v in enumerate(value)]
    if isinstance(value, tuple):
        return tuple(
            _accept_value(v, f"{path}[{i}]", suggestions, exclude) for i, v in enumerate(value)
        )
    return value


def gate_default_drift(
    stage: str,
    params: BaseModel,
    suggestions: Mapping[str, Any] | None = None,
    *,
    exclude: Sequence[str] = (),
) -> tuple[ReportItem, ...]:
    """Warn for each accepted default whose stored value differs from the current default.

    For a `suggested` gate the current default is its suggestion; without `suggestions` (recipe
    load) or without an entry for the path it is skipped.
    """
    items: list[ReportItem] = []
    for path, spec in sorted(gate_paths(params).items()):
        if _excluded(path, exclude):
            continue
        value = get_path(params, path)
        if not isinstance(value, AcceptDefault):
            continue
        if spec.suggested:
            if suggestions is None or path not in suggestions:
                continue
            current = suggestions[path]
        else:
            current = spec.default
        if canonical_json(value.value) != canonical_json(current):
            target = f"{stage}.{path}"
            items.append(
                ReportItem(
                    "gate_default_changed",
                    target,
                    1,
                    {"stored": value.value, "current": current},
                    warning=(
                        f"Gate '{target}' was accepted with default {value.value!r}, but the "
                        f"current default is {current!r}. Review and re-accept if intended."
                    ),
                )
            )
    return tuple(items)


# ---- recipe tree ----


class SourceFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str  # relative to the project root, posix
    sha256: str


class RecipeSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    min_obs_per_cell: int  # value used (A9)


class Recipe(BaseModel):
    """Replayable choices. Deterministic content only: no timestamps."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = CURRENT
    sources: list[SourceFile] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)  # Vocabulary.snapshot(used)
    settings: RecipeSettings
    stages: dict[str, dict[str, Any]] = Field(default_factory=dict)  # one section per stage


def _check_source_path(path: str) -> None:
    posix = PurePosixPath(path)
    if posix.is_absolute() or PureWindowsPath(path).is_absolute() or ".." in posix.parts:
        raise ValueError(f"Source file '{path}' must be inside the project folder.")


def assemble_recipe(
    *,
    sources: Sequence[SourceFile],
    config_snapshot: Mapping[str, Any],
    settings: RecipeSettings,
    params: Mapping[str, BaseModel],
) -> Recipe:
    """The single path that turns typed state into a `Recipe` (stage dicts are made here only)."""
    for source in sources:
        _check_source_path(source.path)
    return Recipe(
        sources=sorted(sources, key=lambda s: s.path),
        config=dict(config_snapshot),
        settings=settings,
        stages={name: p.model_dump(mode="json") for name, p in sorted(params.items())},
    )


def build_recipe(
    *,
    project_root: Path,
    source_paths: Sequence[Path],
    config_snapshot: Mapping[str, Any],
    settings: RecipeSettings,
    stages: Mapping[str, BaseModel],
) -> Recipe:
    root = Path(project_root).resolve()
    sources: list[SourceFile] = []
    for p in source_paths:
        resolved = Path(p).resolve()
        try:
            rel = resolved.relative_to(root)
        except ValueError:
            raise ValueError(
                f"Source file '{p}' must be inside the project folder '{root}'."
            ) from None
        sources.append(SourceFile(path=rel.as_posix(), sha256=file_sha256(resolved)))
    return assemble_recipe(
        sources=sources, config_snapshot=config_snapshot, settings=settings, params=stages
    )


def recipe_hash(recipe: Recipe) -> str:
    return hashlib.sha256(canonical_json(recipe.model_dump(mode="json")).encode()).hexdigest()


def recipe_to_json(recipe: Recipe) -> str:
    return json.dumps(recipe.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


class RecipeError(ValueError):
    """The recipe holds settings that cannot be used; `problems` are `<field path>: <message>`."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("The recipe has settings that cannot be used: " + "; ".join(problems))


def _problems(exc: ValidationError, prefix: str = "") -> list[str]:
    out: list[str] = []
    for err in exc.errors(include_url=False, include_input=False):
        path = ".".join([*([prefix] if prefix else []), *(str(p) for p in err["loc"])])
        out.append(f"{path or '(whole file)'}: {err['msg']}")
    return out


@dataclass(frozen=True)
class LoadedRecipe:
    recipe: Recipe
    params: Mapping[str, BaseModel]
    report: tuple[ReportItem, ...]


def load_recipe(
    text: str,
    stage_params: Mapping[str, type[BaseModel]],
    *,
    registry: Mapping[int, Migration] | None = None,
) -> LoadedRecipe:
    """Parse, migrate and validate a recipe; report migrations and gate-default drift."""
    raw = json.loads(text)
    migrated, report = migrate(raw, registry=registry)
    try:
        recipe = Recipe.model_validate(migrated)
    except ValidationError as exc:
        raise RecipeError(_problems(exc)) from exc
    unknown = sorted(set(recipe.stages) - set(stage_params))
    if unknown:
        raise ValueError(
            f"The recipe has settings for stage(s) this app does not know: {', '.join(unknown)}."
        )
    params: dict[str, BaseModel] = {}
    drift: list[ReportItem] = []
    problems: list[str] = []
    for name, cls in sorted(stage_params.items()):
        try:
            params[name] = cls.model_validate(recipe.stages.get(name, {}))
        except ValidationError as exc:
            problems.extend(_problems(exc, f"stages.{name}"))
            continue
        drift.extend(gate_default_drift(name, params[name]))
    if problems:
        raise RecipeError(problems)
    return LoadedRecipe(recipe, params, (*report, *drift))
