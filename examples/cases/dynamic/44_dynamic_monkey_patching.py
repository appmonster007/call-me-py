"""CASE: replacing methods at runtime (class attribute, instance attribute, setattr, MethodType).
WHY: the same call site run(a) reaches different callees over time; flow-insensitive analyses must merge all.
EXPECTED (ground truth from a traced run; caller -> callee):
  <lambda>@37 -> helper
  <lambda>@41 -> helper
  <module> -> run
  patched -> helper
  run -> <lambda>@37
  run -> <lambda>@41
  run -> A.m
  run -> patched
"""
import types


def helper():
    return 1


class A:
    def m(self):
        return "orig"


def patched(self):
    return helper()


def run(a):
    return a.m()


a = A()
run(a)
A.m = patched
run(a)
a.m = lambda: helper()
run(a)
setattr(A, "m", patched)
b = A()
b.m = types.MethodType(lambda self: helper(), b)
run(b)
