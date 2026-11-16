"""CASE: nested functions, closures, nonlocal.
WHY: inner functions are values returned out of their scope; free variables.
NOTE: nested functions are named outer.inner (no <locals>).
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> counter
  <module> -> counter.bump
  <module> -> make
  <module> -> make.inner
  counter.bump -> log
  make.inner -> helper
"""


def helper(x):
    return x + 1


def log(v):
    return v


def make(n):
    def inner(x):
        return helper(x) + n
    return inner


def counter():
    count = 0

    def bump():
        nonlocal count
        count += 1
        return log(count)
    return bump


add = make(5)
add(1)
c = counter()
c()
c()
