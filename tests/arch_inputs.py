"""Sample inputs for the architecture tests: stage name -> builder returning (inputs, params)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

ARCH_INPUTS: dict[str, Callable[[], tuple[Any, Any]]] = {}
