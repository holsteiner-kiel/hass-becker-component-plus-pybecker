"""Translation file consistency tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRANSLATIONS = ROOT / "translations"


def _leaf_keys(value: dict[str, Any], prefix: str = "") -> set[str]:
    """Return dotted paths for all translation leaves."""
    result: set[str] = set()
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            result.update(_leaf_keys(child, path))
        else:
            result.add(path)
    return result


def _load(language: str) -> dict[str, Any]:
    """Load one bundled translation."""
    return json.loads((TRANSLATIONS / f"{language}.json").read_text(encoding="utf-8"))


def test_english_and_german_translation_keys_match() -> None:
    """Ensure bundled languages expose the same translation surface."""
    assert _leaf_keys(_load("en")) == _leaf_keys(_load("de"))


def test_strings_and_bundled_translation_keys_match() -> None:
    """Keep source strings and bundled languages structurally aligned."""
    strings = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))
    assert _leaf_keys(strings) == _leaf_keys(_load("en"))
    assert _leaf_keys(strings) == _leaf_keys(_load("de"))
