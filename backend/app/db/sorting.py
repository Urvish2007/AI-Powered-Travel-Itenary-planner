"""Typed sort descriptors for Beanie queries.

Beanie's own idiom for a descending sort is ``.sort(-Model.field)``: the unary
minus on an ``ExpressionField`` yields the ``(field, SortDirection)`` tuple the
query builder wants. It works, but the field is *annotated* with its Python type,
so a type checker reads ``-Model.created_at`` as negating a ``datetime`` and
rejects it.

Spelling the sort as the literal string ``"-created_at"`` would type-check, but a
field rename would then leave a silently wrong sort: Mongo ignores a sort on a
field that does not exist, so the list comes back in natural order with no error.
Going through the model attribute keeps that mistake loud — the attribute lookup
itself fails — which is worth one small indirection.
"""

from __future__ import annotations

from typing import Any, cast

from beanie.odm.enums import SortDirection

SortSpec = tuple[str, SortDirection]


def descending(field: Any) -> SortSpec:
    """Sort newest/highest first, e.g. ``descending(Trip.created_at)``."""
    return cast(SortSpec, -field)
