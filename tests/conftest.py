"""Shared pytest configuration and fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config import load_config
from core.vocab import Vocabulary

FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"


@pytest.fixture(scope="session")
def arch_vocab() -> Vocabulary:
    """Vocabulary over the synthetic fixture config (never `config/defaults/`, A9)."""
    return Vocabulary(load_config([FIXTURE_CONFIG]))
