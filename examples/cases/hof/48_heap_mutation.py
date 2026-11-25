"""CASE: functions stored after creation (append through an alias, a callee filling a list, a registry
decorator, dict.values(), per-object fields, a global set by another function), then called elsewhere.
WHY: the store and the call go through different references, so points-to needs a heap.
NOTE: each pattern sits in its own via_* function, so a miss shows up as a missing edge.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> plugin
  <module> -> plugin.deco
  <module> -> via_alias
  <module> -> via_field
  <module> -> via_global
  <module> -> via_param
  <module> -> via_registry
  <module> -> via_values
  handle -> h
  via_alias -> f
  via_field -> Holder.__init__
  via_field -> f
  via_global -> h
  via_global -> install
  via_param -> g
  via_param -> register
  via_registry -> handle
  via_values -> f
"""


def f():
    return "f"


def g():
    return "g"


def h():
    return "h"


def via_alias():
    a = []
    b = a
    b.append(f)
    return a[0]()


def register(reg, fn):
    reg.append(fn)


def via_param():
    handlers = []
    register(handlers, g)
    for cb in handlers:
        cb()


REG = {}


def plugin(name):
    def deco(fn):
        REG[name] = fn
        return fn
    return deco


@plugin("h")
def handle():
    return h()


def via_registry():
    return REG["h"]()


def via_values():
    table = {}
    table["f"] = f
    for fn in table.values():
        fn()


class Holder:
    def __init__(self, cb):
        self.cb = cb


def via_field():
    Holder(g)  # a second object with another callback
    return Holder(f).cb()


hook = None


def install():
    global hook
    hook = h


def via_global():
    install()
    return hook()


via_alias()
via_param()
via_registry()
via_values()
via_field()
via_global()
