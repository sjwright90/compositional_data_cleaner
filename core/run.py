"""Headless pipeline runner (`python -m core.run --project <dir>`; see --help)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core.config import DEFAULT_CONFIG_DIR, PLACEHOLDER_WARNING
from core.project import ERROR_LOG_FILE, PLAIN_ERRORS, Project, ProjectError
from core.recipe import RecipeError
from core.stages import STAGES

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNRESOLVED_GATES = 2

_EPILOG = """\
exit codes:
  0  success: every stage ran (or was unchanged) and was committed
  1  error: a locked file, an unreadable or unsupported recipe, bad configuration, or a
     missing project folder (the message says what to fix); also an internal error in a
     stage, whose traceback is appended to <project>/log/errors.log
  2  unresolved gates: the run stopped and listed every gate field that still needs a
     decision (set it in the recipe, or use --accept-defaults in tests)
"""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m core.run",
        description="Run the water-chemistry pipeline headlessly from a recipe.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--project", type=Path, required=True, help="project folder")
    p.add_argument(
        "--recipe",
        type=Path,
        default=None,
        help="recipe file to read and write (default: <project>/recipe.json)",
    )
    p.add_argument(
        "--accept-defaults",
        action="store_true",
        help="tests only (A3): record an explicit accepted default for every unresolved gate",
    )
    return p


def _describe_error(exc: Exception) -> str:
    if isinstance(exc, json.JSONDecodeError):
        return f"The recipe file is not valid JSON ({exc.msg}, line {exc.lineno})."
    return str(exc) or exc.__class__.__name__


def _describe_problems(problems: Sequence[str], recipe_file: Path) -> str:
    lines = [f"The recipe file '{recipe_file}' has settings that cannot be used:"]
    lines.extend(f"  - {p}" for p in problems)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, stages: Sequence[type[Any]] = STAGES) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args, stages)
    except (ValidationError, RecipeError) as exc:  # before ValueError: both are subclasses
        recipe_file = args.recipe if args.recipe else args.project / "recipe.json"
        if isinstance(exc, RecipeError):
            problems = exc.problems
        else:
            problems = [
                f"{'.'.join(str(p) for p in e['loc']) or '(whole file)'}: {e['msg']}"
                for e in exc.errors(include_url=False, include_input=False)
            ]
        print(f"Error: {_describe_problems(problems, recipe_file)}", file=sys.stderr)
        return EXIT_ERROR
    except (ValueError, OSError, ProjectError) as exc:
        print(f"Error: {_describe_error(exc)}", file=sys.stderr)
        return EXIT_ERROR


def _run(args: argparse.Namespace, stages: Sequence[type[Any]]) -> int:
    project = Project(
        args.project, stages=stages, defaults_dir=DEFAULT_CONFIG_DIR, recipe_path=args.recipe
    )
    if project.placeholder:
        print(f"Note: {PLACEHOLDER_WARNING}.")
    for item in project.notices:
        if item.action != "placeholder_config":
            print(f"Note: {item.warning or f'{item.action}: {item.target}'}")

    if args.accept_defaults:
        accepted = project.accept_defaults()
        print(
            f"WARNING: --accept-defaults is for tests only (A3); accepted {len(accepted)} "
            "default(s) without review."
        )
        if accepted:
            project.write_recipe()

    for name in project.stage_names:
        if any(g.startswith(f"{name}.") for g in project.unresolved_gates()):
            break
        if project.heads.get(name) == project.fingerprint(name):
            print(f"{name}: unchanged, not recommitted")
            continue
        try:
            checkpoint = project.commit(name)
        except PLAIN_ERRORS:
            raise
        except Exception as exc:  # unexpected: keep the traceback in the log, not on screen
            project.record_error(name, exc)
            detail = (str(exc).splitlines() or [""])[0]
            summary = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
            print(
                f"Internal error in stage {name}: {summary}; "
                f"details in {args.project / ERROR_LOG_FILE}",
                file=sys.stderr,
            )
            return EXIT_ERROR
        print(f"{name}: committed ({checkpoint.name})")

    unresolved = project.unresolved_gates()
    if unresolved:
        print("The run stopped: these gate fields still need a decision:")
        for field in unresolved:
            print(f"  {field}")
        return EXIT_UNRESOLVED_GATES

    project.write_recipe()
    print(f"Recipe written to {project.recipe_path}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
