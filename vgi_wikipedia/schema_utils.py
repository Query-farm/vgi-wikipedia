"""Shared Arrow-schema helpers for the Wikipedia worker.

Keeps the column-comment plumbing in one place so every function exposes a
consistent, documented schema to DuckDB.
"""

from __future__ import annotations

import pyarrow as pa


def field(
    name: str,
    type: pa.DataType,  # noqa: A002 - mirrors pa.field's own parameter name
    comment: str,
    *,
    nullable: bool = True,
) -> pa.Field:
    """Build a ``pa.Field`` carrying a column comment in its metadata.

    The ``comment`` metadata key is the framework's transport for column
    comments -- DuckDB surfaces it via ``duckdb_columns()`` and ``DESCRIBE``.
    """
    return pa.field(
        name,
        type,
        nullable=nullable,
        metadata={b"comment": comment.encode("utf-8")},
    )
