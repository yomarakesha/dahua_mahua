"""Guard against the duplicate-field regression in Settings.

`reencode_*` were once defined twice in settings.py; Python keeps the LAST, so
the earlier (richer) definition — including reencode_vcodec's auto-probe intent —
was silently shadowed. Parse the source (annotations dedupe, so we must read the
class body via ast) and assert each reencode_* field is declared exactly once.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import app.settings as settings_mod

SETTINGS_PY = Path(settings_mod.__file__)


def _annotated_field_names() -> list[str]:
    """Every annotated-assignment target in the Settings class body (source-level,
    so a duplicate declaration shows up twice — a plain dict/annotations would
    dedupe it away)."""
    tree = ast.parse(SETTINGS_PY.read_text(encoding="utf-8"))
    cls = next(
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "Settings"
    )
    names: list[str] = []
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.append(stmt.target.id)
    return names


def test_reencode_fields_declared_exactly_once():
    counts = Counter(n for n in _annotated_field_names() if n.startswith("reencode_"))
    assert counts, "expected reencode_* settings fields to exist"
    dupes = {name: c for name, c in counts.items() if c != 1}
    assert not dupes, f"reencode_* fields declared more than once: {dupes}"


def test_no_setting_field_declared_twice():
    counts = Counter(_annotated_field_names())
    dupes = {name: c for name, c in counts.items() if c > 1}
    assert not dupes, f"duplicate Settings fields: {dupes}"


def test_effective_vcodec_default_is_libx264():
    # Deployed behavior must be unchanged: default stays the portable CPU encoder;
    # "auto" is opt-in via env.
    assert settings_mod.Settings().reencode_vcodec == "libx264"
