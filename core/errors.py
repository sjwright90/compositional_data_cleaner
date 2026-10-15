"""Base for the project's plain-language errors (import-free, so any core module can use it)."""

from __future__ import annotations


class ProjectError(RuntimeError):
    """Base for the project's own plain-language errors; the runner prints them as they are."""
