"""Test-suite defaults.

**Tests do not write traces.** The Langfuse project is the submission channel: the
instructor is given Member access and reads the twenty-seven runs listed in
`SUBMISSION.md`. A suite that emits traces fills that project with runs nobody asked
for — measured before this file existed, ten of the last forty traces came from the
stub graph in `test_rearm.py`, carrying a span called `reply` that exists nowhere in
the real agent.

Clearing the credentials is enough: `observability.tracing.is_configured()` returns
False, `bind_callbacks` hands back the config untouched, and `turn_span` yields a null
span. So the agent runs in tests exactly as it does in production, minus the reporting.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True, scope="session")
def _no_tracing_from_tests() -> None:
    """Unset Langfuse credentials for the whole suite."""

    import os

    saved = {
        key: os.environ.pop(key, None)
        for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST",
                    "LANGFUSE_BASE_URL")
    }
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
