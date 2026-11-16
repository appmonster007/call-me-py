"""CASE: functools.partial and lru_cache wrappers.
WHY: the wrapper object is C code; the real callee is hidden behind a value.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> add
  <module> -> fib
  <module> -> run
  <module> -> slow
  fib -> fib
  run -> add
"""
import functools


def add(a, b):
    return a + b


def slow(n):
    return n


@functools.lru_cache(maxsize=None)
def fib(n):
    return n if n < 2 else fib(n - 1) + fib(n - 2)


def run(cb):
    return cb(1)


add5 = functools.partial(add, 5)
add5(1)
cached = functools.lru_cache(maxsize=None)(slow)
cached(2)
run(add5)
fib(3)
