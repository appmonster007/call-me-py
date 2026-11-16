"""CASE: functions flowing through lists, dicts, attributes, tuple swaps, copies and nested containers.
WHY: points-to must track values through the heap and through aliases.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> f
  <module> -> g
  <module> -> get
  <module> -> h
  <module> -> ident
"""


def f():
    return "f"


def g():
    return "g"


def h():
    return "h"


def ident(x):
    return x


def get(box):
    return box["fn"]


lst = []
lst.append(f)
lst[0]()
d = {}
d["k"] = g
d["k"]()


class Holder:
    pass


o = Holder()
o.fn = h
o.fn()
a, b = f, g
a, b = b, a
a()
b()
alias = lst
alias.append(g)
alias[1]()
copy = list(lst)
copy[0]()
chain = [[f], [g]]
chain[1][0]()
ident(h)()
get({"fn": f})()
