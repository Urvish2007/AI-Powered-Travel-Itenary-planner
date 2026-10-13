"""Shared response envelopes and query primitives."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Query
from pydantic import BaseModel, Field


class Pagination(BaseModel):
    """Offset pagination parameters.

    Always consumed as ``PaginationDep`` (i.e. ``Depends()``), never as
    ``Annotated[Pagination, Query()]``: FastAPI only binds a Query model when
    it is the *only* query parameter on the endpoint, and silently treats it
    as a single missing field as soon as another one is added.
    """

    skip: Annotated[int, Query(ge=0, le=100_000)] = 0
    limit: Annotated[int, Query(ge=1, le=100)] = 20


PaginationDep = Annotated[Pagination, Depends()]


class PageMeta(BaseModel):
    total: int = Field(ge=0)
    skip: int = Field(ge=0)
    limit: int = Field(ge=1)
    has_more: bool = False


class Page[T](BaseModel):
    """A paginated collection response."""

    items: list[T]
    meta: PageMeta

    @classmethod
    def build(cls, items: list[T], total: int, skip: int, limit: int) -> Page[T]:
        return cls(
            items=items,
            meta=PageMeta(
                total=total,
                skip=skip,
                limit=limit,
                has_more=skip + len(items) < total,
            ),
        )


class Message(BaseModel):
    """A plain acknowledgement, for endpoints with nothing else to return."""

    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """The body every failing endpoint returns. Documented so clients can rely on it."""

    error: ErrorDetail


class HealthStatus(BaseModel):
    status: str
    version: str
    environment: str
    mongodb: str
    checks: dict[str, Any] = Field(default_factory=dict)
