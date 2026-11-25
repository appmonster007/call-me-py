"""CASE: function factories, composition, currying.
WHY: callees are created by other functions; nested lambdas.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> compose
  <module> -> compose.h
  <module> -> curry
  <module> -> curry.<lambda>@41
  <module> -> curry.<lambda>@42
  <module> -> make_adder
  <module> -> make_adder.<lambda>@31
  compose.h -> double
  compose.h -> inc
  curry.<lambda>@42 -> add
  inc -> add
"""


def add(a, b):
    return a + b


def inc(x):
    return add(x, 1)


def double(x):
    return x * 2


def make_adder(n):
    return lambda x: x + n


def compose(f, g):
    def h(x):
        return f(g(x))
    return h


def curry(f):
    return lambda a: (
        lambda b: f(a, b)
    )


make_adder(1)(2)
compose(inc, double)(3)
curry(add)(1)(2)
