"""CASE: lambdas: stored, immediately invoked, in containers, in comprehensions.
WHY: anonymous callees need a stable name; here <lambda>@<def line>.
EXPECTED (ground truth from a traced run; caller -> callee):
  <lambda>@21 -> inc
  <lambda>@23 -> inc
  <lambda>@25 -> inc
  <lambda>@26 -> inc
  <lambda>@30 -> inc
  <module> -> <lambda>@21
  <module> -> <lambda>@23
  <module> -> <lambda>@25
  <module> -> <lambda>@26
  <module> -> <lambda>@30
"""


def inc(x):
    return x + 1


f = lambda x: inc(x)
f(1)
(lambda: inc(2))()
fs = [
    lambda: inc(3),
    lambda: inc(4),
]
for g in fs:
    g()
adders = [lambda x, i=i: inc(x) + i for i in range(2)]
for a in adders:
    a(0)
