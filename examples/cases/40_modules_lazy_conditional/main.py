"""CASE: TYPE_CHECKING-only import, try/except ImportError fallback, import inside a function, __import__ by name.
WHY: a name may be bound by several imports/definitions; imports can execute lazily.
STATIC_ONLY (the fallback definition is dead because fast.py exists):
  main.<module> -> main.compute
EXPECTED (ground truth from a traced run; caller -> callee):
  main.<module> -> fast.compute
  main.<module> -> main.dyn
  main.<module> -> main.lazy
  main.dyn -> slow.work
  main.lazy -> slow.work
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from heavy import Heavy  # noqa: F401

try:
    from fast import compute
except ImportError:
    def compute(x):
        return x


def lazy():
    from slow import work
    return work(1)


def dyn(name):
    mod = __import__(name)
    return mod.work(2)


compute(1)
lazy()
dyn("slow")
