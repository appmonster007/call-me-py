"""CASE: callbacks handed to builtin / stdlib higher-order functions.
WHY: the callee runs inside C code; the traced caller is the Python function that passed it.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> add
  <module> -> is_pos
  <module> -> key
  <module> -> sq
  <module> -> total
  total -> sq
"""
from functools import reduce


def sq(x):
    return x * x


def is_pos(x):
    return x > 0


def key(x):
    return -x


def add(a, b):
    return a + b


def total(xs):
    return sum(map(sq, xs))


xs = [3, -1, 2]
list(map(sq, xs))
list(filter(is_pos, xs))
sorted(xs, key=key)
max(xs, key=key)
min(xs, key=key)
reduce(add, xs)
xs.sort(key=key)
any(is_pos(x) for x in xs)
total(xs)
