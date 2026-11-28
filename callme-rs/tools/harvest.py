# pyright: basic
"""pytest plugin: record every source string the Python tests analyse, for `parity.py`.

The Python unit tests mostly analyse small snippets with `analyze_source` and
`build_program`. Recording them turns every Python test, including tests added later, into
a parity check of the Rust port without porting the test itself:

    cd <python root>
    PYTHONPATH=.:<crate>/tools CALLME_HARVEST=/tmp/snippets.json \\
        python -m pytest -p harvest tests --families all -q
    python3 <crate>/tools/parity.py --snippets /tmp/snippets.json

The plugin wraps the two functions before the test modules import them, and writes the
recorded sources (deduplicated, in first-seen order) to `$CALLME_HARVEST` at the end of the
session.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

import callme
import callme.frontend
import callme.frontend.builder

RECORDED: dict[str, dict[str, str]] = {}


def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Return `fn`, recording its first argument when it is a source string."""
    def inner(source: object, *args: object, **kwargs: object) -> Any:
        """Record `source`, then call the wrapped function."""
        if isinstance(source, str):
            RECORDED.setdefault(source, {"source": source})
        return fn(source, *args, **kwargs)
    return inner


callme.analyze_source = _wrap(callme.analyze_source)
callme.frontend.build_program = _wrap(callme.frontend.build_program)
callme.frontend.builder.build_program = _wrap(callme.frontend.builder.build_program)


def pytest_sessionfinish(session: object) -> None:
    """Write the recorded sources to `$CALLME_HARVEST` (default `harvest.json`)."""
    out = os.environ.get("CALLME_HARVEST", "harvest.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(list(RECORDED.values()), f, indent=1)
