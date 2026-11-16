"""CASE: dispatch dicts, lists of callbacks, tuple unpacking, observer registry.
WHY: callees live in containers; the container is index-insensitive for most analyses.
NOTE: OPS[op](a, b) statically targets add, sub and the lambda for every op.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> Bus.__init__
  <module> -> Bus.emit
  <module> -> Bus.on
  <module> -> add
  <module> -> calc
  <module> -> default
  <module> -> sub
  Bus.emit -> add
  Bus.emit -> sub
  calc -> <lambda>@32
  calc -> add
  calc -> sub
"""


def add(a, b):
    return a + b


def sub(a, b):
    return a - b


def default():
    return 0


OPS = {"add": add, "sub": sub, "mul": lambda a, b: a * b}
HANDLERS = [add, sub]


def calc(op, a, b):
    return OPS[op](a, b)


class Bus:
    def __init__(self):
        self.subs = []

    def on(self, cb):
        self.subs.append(cb)

    def emit(self, *a):
        for cb in self.subs:
            cb(*a)


calc("add", 1, 2)
calc("sub", 1, 2)
calc("mul", 1, 2)
OPS.get("zzz", default)()
for h in HANDLERS:
    h(1, 2)
first, second = HANDLERS
first(1, 1)
bus = Bus()
bus.on(add)
bus.on(sub)
bus.emit(1, 2)
