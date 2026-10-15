"""Recipe tree and gate fields (spec §8; addendum A3, A9)."""

from __future__ import annotations

import hashlib
import json
import logging
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


# ---- gates ----


class AcceptDefault(BaseModel, Generic[T]):
    """`{"accept_default": true, "value": <resolved default>}` (refines A3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accept_default: Literal[True]
    value: T


# A gate field is unresolved (None), an explicit decision (T) or an accepted default.
Gate = Union[T, AcceptDefault[T], None]  # noqa: UP007  (TypeVar union must stay subscriptable)


def gate_field(default: Any) -> Any:
    """Declare a gate on a stage's `Params`; `default` is the value accepting it records."""
    return Field(default=None, json_schema_extra={_GATE_DEFAULT_KEY: default})


@dataclass(frozen=True)
class GateSpec:
    default: Any


def _is_gate(annotation: Any) -> bool:
    if get_origin(annotation) not in (Union, types.UnionType):
        return False
    return any(isinstance(a, type) and issubclass(a, AcceptDefault) for a in get_args(annotation))


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def gate_fields(params_cls: type[BaseModel], prefix: str = "") -> dict[str, GateSpec]:
    """Dotted field path -> spec, for every gate on a Params model (recursing into sub-models)."""
    out: dict[str, GateSpec] = {}
    for name, info in params_cls.model_fields.items():
        path = f"{prefix}{name}"
        if _is_gate(info.annotation):
            extra = info.json_schema_extra
            if not isinstance(extra, dict) or _GATE_DEFAULT_KEY not in extra:
                raise TypeError(f"Gate field '{path}' must be declared with gate_field(default).")
            out[path] = GateSpec(extra[_GATE_DEFAULT_KEY])
            continue
        nested = _nested_model(info.annotation)
        if nested is not None:
            out.update(gate_fields(nested, f"{path}."))
    return out


def _get(params: BaseModel, path: str) -> Any:
    obj: Any = params
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def unresolved_gates(stage: str, params: BaseModel) -> list[str]:
    """`<stage>.<dotted.field>` for every gate still unresolved, sorted."""
    return sorted(
        f"{stage}.{path}" for path in gate_fields(type(params)) if _get(params, path) is None
    )


def accept_defaults(params: M) -> M:
    """New params with every unresolved gate set to an explicit accepted default."""
    return _accept(params)


def _accept(params: M) -> M:
    update: dict[str, Any] = {}
    for name, info in type(params).model_fields.items():
        value = getattr(params, name)
        if _is_gate(info.annotation):
            if value is None:
                extra = info.json_schema_extra
                assert isinstance(extra, dict)
                update[name] = AcceptDefault[Any](
                    accept_default=True, value=extra[_GATE_DEFAULT_KEY]
                )
        elif isinstance(value, BaseModel):
            update[name] = _accept(value)
    return params.model_copy(update=update)


def gate_default_drift(stage: str, params: BaseModel) -> tuple[ReportItem, ...]:
    """Warn for each accepted default whose stored value differs from the current default."""
    items: list[ReportItem] = []
    for path, spec in sorted(gate_fields(type(params)).items()):
        value = _get(params, path)
        if not isinstance(value, AcceptDefault):
            continue
        if canonical_json(value.value) != canonical_json(spec.default):
            target = f"{stage}.{path}"
            items.append(
                ReportItem(
                    "gate_default_changed",
                    target,
                    1,
                    {"stored": value.value, "current": spec.default},
                    warning=(
                        f"Gate '{target}' was accepted with default {value.value!r}, but the "
                        f"current default is {spec.default!r}. Review and re-accept if intended."
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
