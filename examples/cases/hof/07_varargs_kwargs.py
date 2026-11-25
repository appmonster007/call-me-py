"""CASE: *args / **kwargs forwarding, keyword binding, argument unpacking.
WHY: callees flow through variadic parameters and keyword names.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> by_kw
  <module> -> call_all
  <module> -> forward
  by_kw -> b
  by_kw -> c
  call_all -> b
  call_all -> c
  forward -> b
  forward -> c
"""


def b(x):
    return x


def c(x, y=0):
    return x + y


def forward(f, *args, **kwargs):
    return f(*args, **kwargs)


def call_all(*fns):
    for f in fns:
        f(1)


def by_kw(f=None, v=0):
    return f(v)


forward(b, 1)
forward(c, 1, y=2)
call_all(b, c)
by_kw(v=2, f=b)
opts = {"f": c, "v": 5}
by_kw(**opts)
pos = (b, 7)
by_kw(*pos)
