"""CASE: direct calls between module-level functions.
WHY: baseline; every sound analysis must find these.
STATIC_ONLY (dead code a static analysis still reports):
  uncalled -> leaf
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> top
  mid -> leaf
  top -> mid
"""


def leaf():
    return 1


def mid():
    return leaf() + leaf()


def top():
    return mid()


def uncalled():
    return leaf()


top()
