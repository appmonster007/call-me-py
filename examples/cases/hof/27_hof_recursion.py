"""CASE: recursion through higher-order functions (fixpoint combinator, trampoline, memoize decorator).
WHY: cycles in the call graph that only exist through function values.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> fix
  <module> -> fix.g
  <module> -> memo
  <module> -> memo.w
  <module> -> trampoline
  countdown.<lambda>@37 -> countdown
  fact_step -> fix.g
  fib -> memo.w
  fix.g -> fact_step
  memo.w -> fib
  trampoline -> countdown
  trampoline -> countdown.<lambda>@37
"""


def fix(f):
    def g(*a):
        return f(g, *a)
    return g


def fact_step(rec, n):
    return 1 if n <= 1 else n * rec(n - 1)


def trampoline(fn, *a):
    r = fn(*a)
    while callable(r):
        r = r()
    return r


def countdown(n):
    return 0 if n == 0 else (lambda: countdown(n - 1))


def memo(fn):
    cache = {}

    def w(n):
        if n not in cache:
            cache[n] = fn(n)
        return cache[n]
    return w


@memo
def fib(n):
    return n if n < 2 else fib(n - 1) + fib(n - 2)


fix(fact_step)(3)
trampoline(countdown, 2)
fib(4)
