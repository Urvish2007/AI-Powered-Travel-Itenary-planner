"""Every setting is documented in .env.example, and nothing there is stale.

A setting that exists only in code is one an operator cannot find; a variable
that exists only in the example is one they set for nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings

EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"


def _env_names(field_name: str) -> list[str]:
    alias = Settings.model_fields[field_name].validation_alias
    choices = getattr(alias, "choices", None)
    return [str(choice) for choice in choices] if choices else [field_name.upper()]


def _documented() -> set[str]:
    text = EXAMPLE.read_text(encoding="utf-8")
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, re.MULTILINE))


def test_every_setting_appears_in_the_env_example() -> None:
    documented = _documented()
    missing = [
        name
        for name in Settings.model_fields
        if not any(env in documented for env in _env_names(name))
    ]
    assert not missing, f"add these to backend/.env.example: {missing}"


def test_the_env_example_names_no_unknown_settings() -> None:
    known = {env for name in Settings.model_fields for env in _env_names(name)}
    stale = sorted(_documented() - known)
    assert not stale, f"not settings any more, remove from .env.example: {stale}"
