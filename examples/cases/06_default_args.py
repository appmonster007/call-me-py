"""CASE: functions used as default argument values.
WHY: the callee reaches the parameter without any argument at the call site;
     defaults are evaluated once, at definition time.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> apply
  <module> -> apply2
  <module> -> apply3
  <module> -> compute
  apply -> double
  apply -> square
  apply2 -> square
  apply3 -> apply3.<lambda>@37
  apply3.<lambda>@37 -> double
"""


def square(x):
    return x * x


def double(x):
    return x * 2


def compute():
    return square


def apply(v, cb=square):
    return cb(v)


def apply2(v, cb=compute()):
    return cb(v)


def apply3(v, cb=lambda x: double(x)):
    return cb(v)


apply(2)
apply(2, double)
apply2(3)
apply3(4)
