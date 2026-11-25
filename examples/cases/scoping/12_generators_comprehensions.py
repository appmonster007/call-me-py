"""CASE: calls inside comprehensions, generator functions, yield from, generator expressions.
WHY: comprehension/generator frames are not real callers; calls inside belong to the enclosing function.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> f
  <module> -> gen
  <module> -> outer
  <module> -> pred
  gen -> f
  outer -> gen
"""


def f(x):
    return x + 1


def pred(x):
    return x > 1


def gen(n):
    for i in range(n):
        yield f(i)


def outer(n):
    yield from gen(n)


[f(x) for x in range(2)]
{x: f(x) for x in range(2)}
list(gen(2))
list(outer(2))
sum(f(x) for x in range(3) if pred(x))
g = gen(1)
next(g)
