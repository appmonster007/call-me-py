"""CASE: functions passed as arguments and returned from selectors.
WHY: classic higher-order call; apply() has two callees depending on its caller.
NOTE: context-insensitive analyses add apply->square and apply->double for every call site (still sound).
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> apply
  <module> -> double
  <module> -> pick
  <module> -> square
  <module> -> twice
  apply -> double
  apply -> square
  twice -> square
"""


def square(x):
    return x * x


def double(x):
    return x * 2


def apply(f, v):
    return f(v)


def twice(f, v):
    return f(f(v))


def pick(flag):
    return square if flag else double


apply(square, 1)
apply(double, 2)
twice(square, 2)
pick(True)(3)
pick(False)(3)
