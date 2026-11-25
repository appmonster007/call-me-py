"""CASE: calls that are never executed (if False, version checks, after return, except branch).
WHY: a static analysis keeps these edges (over-approximation); a dynamic trace never sees them.
STATIC_ONLY (reported statically, never executed):
  main -> never
  gated -> never
  after_return -> never
  uncalled -> never
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> main
  after_return -> always
  gated -> always
  main -> after_return
  main -> gated
"""
import sys


def always():
    return 1


def never():
    return 2


def uncalled():
    return never()


def after_return():
    return always()
    never()


def gated(flag):
    if flag:
        never()
    return always()


def main():
    if False:
        never()
    if sys.version_info < (3,):
        never()
    gated(False)
    after_return()
    try:
        pass
    except Exception:
        never()
    assert True


main()
