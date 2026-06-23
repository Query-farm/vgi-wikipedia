"""In-process VGI invocation helpers for the Wikipedia worker test suite.

Drives a table function through the real bind -> init -> process lifecycle (no
worker subprocess), and -- crucially -- can re-serialize and restore the scan
state between ticks so the ``sroffset`` pagination cursor's round-trip across
batch boundaries is exercised exactly as it would be over the wire.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa
from vgi.arguments import Arguments
from vgi.function_storage import BoundStorage, FunctionStorageSqlite
from vgi.invocation import FunctionType
from vgi.protocol import BindRequest, InitRequest
from vgi.table_function import ProcessParams


class MockOutputCollector:
    """Captures emitted batches for assertions."""

    def __init__(self, output_schema: pa.Schema) -> None:
        self.output_schema = output_schema
        self.batches: list[pa.RecordBatch] = []
        self._finished = False

    def emit(self, batch: pa.RecordBatch, partition_values: Any = None, metadata: Any = None) -> None:
        self.batches.append(batch)

    def finish(self) -> None:
        self._finished = True

    @property
    def finished(self) -> bool:
        return self._finished

    def emit_client_log_message(self, msg: Any) -> None:
        pass


def _as_scalar(value: Any) -> Any:
    """Wrap a plain Python value as a ``pa.Scalar`` (pass scalars through)."""
    if isinstance(value, pa.Scalar):
        return value
    return pa.scalar(value)


def _process_params(func_cls: type, args: Arguments, *, settings=None, secrets=None) -> ProcessParams:
    bind_req = BindRequest(
        function_name=func_cls.Meta.name,
        arguments=args,
        function_type=FunctionType.TABLE,
    )
    bind_resp = func_cls.bind(bind_req)
    init_req = InitRequest(bind_call=bind_req, output_schema=bind_resp.output_schema)
    init_resp = func_cls.global_init(init_req)
    storage = FunctionStorageSqlite(":memory:")
    return ProcessParams(
        args=func_cls._parse_arguments(func_cls.FunctionArguments, args),
        init_call=init_req,
        init_response=init_resp,
        output_schema=bind_resp.output_schema,
        settings=settings or {},
        secrets=secrets or {},
        storage=BoundStorage(storage, init_resp.execution_id),
    )


def run_table_function(
    func_cls: type,
    *,
    positional: tuple = (),
    named: dict | None = None,
    settings: dict | None = None,
    secrets: dict | None = None,
    serialize_state: bool = False,
) -> pa.Table:
    """Run a (source) table function bind -> init -> process* -> table.

    When ``serialize_state`` is True, the scan state is round-tripped through its
    Arrow serialization between every ``process`` tick -- mimicking the wire
    behaviour and proving the cursor survives batch boundaries.

    ``positional`` / ``named`` values are plain Python values; they are wrapped
    as ``pa.scalar`` here (the framework hands arguments to a worker as Arrow
    scalars over the wire).
    """
    positional_scalars = tuple(_as_scalar(v) for v in positional)
    named_scalars = {k: _as_scalar(v) for k, v in (named or {}).items()}
    args = Arguments(positional=positional_scalars, named=named_scalars)
    params = _process_params(func_cls, args, settings=settings, secrets=secrets)

    state = func_cls.initial_state(params)
    state_type = type(state) if state is not None else None
    out = MockOutputCollector(params.output_schema)

    guard = 0
    while not out.finished:
        guard += 1
        if guard > 1000:
            raise AssertionError("process did not finish within 1000 ticks")
        func_cls.process(params, state, out)
        if serialize_state and state is not None and state_type is not None:
            # Round-trip the state exactly as the framework would across ticks.
            state = state_type.deserialize_from_bytes(state.serialize_to_bytes())

    return pa.Table.from_batches(out.batches, schema=params.output_schema)
