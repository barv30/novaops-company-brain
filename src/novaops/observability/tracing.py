"""Langfuse wiring: named spans, and the metadata a reader needs.

This is the submission channel. The specification is explicit that the project is
assessed by reading the repository and the traces, never by running the code — so a
trace is not a debugging convenience that also happens to be logged. It is the artefact.

What a reader must be able to do with one trace, without reading any code:

  * follow `classify → scope → tools → answer` by span name;
  * see who asked and under what authority;
  * see which tools ran, in order;
  * see the terminal `status`, and whether the write gate opened and why.

`bind_callbacks()` attaches the LangChain handler so LangGraph nodes become spans
automatically. `turn_span()` wraps one turn so the metadata above lands on it. Both are
no-ops when Langfuse is not configured, so the agent runs offline and in tests without
special-casing.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from typing import Any

_WARNED = False


def is_configured() -> bool:
    """Whether Langfuse has credentials to send anything."""

    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def new_request_id() -> str:
    """An id that survives tool calls, checkpoints and resumes.

    Short and readable rather than a bare UUID, because it appears in trace metadata and
    in idempotency keys, and a human has to match them up by eye when marking.
    """

    return f"req-{uuid.uuid4().hex[:12]}"


def _client():
    from langfuse import get_client

    return get_client()


def bind_callbacks(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add the Langfuse callback handler to a LangGraph invocation config.

    Returns the config unchanged when Langfuse is not configured, so the same call site
    works with and without credentials.
    """

    global _WARNED
    config = dict(config or {})
    if not is_configured():
        if not _WARNED:
            _WARNED = True
            print(
                "[tracing] Langfuse is not configured "
                "(LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY). Running untraced."
            )
        return config

    from langfuse.langchain import CallbackHandler

    callbacks = list(config.get("callbacks") or [])
    if not any(type(cb).__name__ == "CallbackHandler" for cb in callbacks):
        callbacks.append(CallbackHandler())
    config["callbacks"] = callbacks
    return config


@contextmanager
def turn_span(
    *,
    name: str,
    caller_id: str,
    user_group: str,
    is_manager: bool,
    scope: str,
    thread_id: str,
    request_id: str,
    turn: int | None = None,
    case_id: str | None = None,
    user_input: str | None = None,
):
    """Wrap one turn, and attach the metadata a marker reads first.

    Yields a small object with `update(**fields)`, used by the caller to record what is
    only known once the turn has finished: the tool sequence, the terminal status, and
    the write gate's decision.
    """

    if not is_configured():
        yield _NullSpan()
        return

    from langfuse import propagate_attributes

    client = _client()
    # NOT "scope". Langfuse reserves that key for its own SDK descriptor, and a value
    # put there is silently replaced by {'name': 'langfuse-sdk', ...} — so the chosen
    # scope, which the specification requires in trace metadata, was absent from every
    # trace until this was found by reading one back through the API.
    metadata = {
        "caller_id": caller_id,
        "user_group": user_group,
        "is_manager": str(is_manager),
        "agent_scope": scope,
        "thread_id": thread_id,
        "request_id": request_id,
    }
    if turn is not None:
        metadata["turn"] = str(turn)
    if case_id:
        metadata["case_id"] = case_id

    with propagate_attributes(session_id=thread_id, user_id=caller_id, metadata=metadata):
        # `agent`, not the default span: the LLM judges (scripts/judges.py) fire on
        # observations of type AGENT, and this is the one observation per turn whose
        # input is the user's message and whose output is the answer. Any other target
        # would judge each model call inside the turn rather than the turn.
        with client.start_as_current_observation(
            as_type="agent", name=name, input=user_input
        ) as span:
            wrapper = _Span(span)
            try:
                yield wrapper
            finally:
                if wrapper.pending:
                    span.update(**wrapper.pending)
                client.flush()


class _Span:
    """Collects updates so the caller does not need to know the Langfuse API."""

    def __init__(self, span) -> None:
        self._span = span
        self.pending: dict[str, Any] = {}
        self.trace_id: str | None = getattr(span, "trace_id", None)

    def update(self, **fields: Any) -> None:
        metadata = fields.pop("metadata", None)
        if metadata:
            merged = dict(self.pending.get("metadata") or {})
            # Scalars become strings, as the marker reads them. A dict stays structured:
            # the judges reach into `trajectory` with a jsonPath, and a stringified dict
            # is one opaque value that no path can enter.
            merged.update(
                {k: v if isinstance(v, dict) else str(v) for k, v in metadata.items()}
            )
            self.pending["metadata"] = merged
        self.pending.update(fields)


class _NullSpan:
    """What `turn_span` yields when Langfuse is not configured."""

    trace_id: str | None = None

    def update(self, **fields: Any) -> None:  # noqa: D102
        return


def score(
    name: str,
    value: float | str,
    *,
    comment: str | None = None,
    data_type: str | None = None,
) -> None:
    """Attach a score to the trace currently open.

    Lesson 11 asks for runs to be scored with deterministic checks first. A trace
    carrying its own score is readable without opening the repository: a marker sees
    "binding_checks 0.93" beside the run rather than having to reconstruct whether it
    was right. Silent when Langfuse is not configured, so scoring a run offline is not
    an error.
    """

    if not is_configured():
        return
    try:
        _client().score_current_trace(
            name=name, value=value, comment=comment, data_type=data_type  # type: ignore[arg-type]
        )
    except Exception:  # noqa: BLE001 — a failed score must never fail the run
        pass


def score_trace(
    trace_id: str,
    name: str,
    value: float | str,
    *,
    comment: str | None = None,
    data_type: str | None = None,
    score_id: str | None = None,
) -> None:
    """Attach a score to a trace that has already closed.

    The evaluation harness scores a turn *after* the span has ended — the checks need
    the finished answer — so scoring by id is the path that actually gets used.

    A fixed `score_id` makes the write an upsert: scoring the same trace again replaces
    its score instead of adding a second one beside it. Idempotent, like every write.
    """

    if not is_configured():
        return
    try:
        client = _client()
        client.create_score(
            name=name, value=value, trace_id=trace_id, comment=comment,
            data_type=data_type,  # type: ignore[arg-type]
            score_id=score_id,
        )
        client.flush()
    except Exception:  # noqa: BLE001
        pass


def trace_url() -> str | None:
    """The URL of the trace currently being written, for the submission index."""

    if not is_configured():
        return None
    try:
        return _client().get_trace_url()
    except Exception:
        return None
