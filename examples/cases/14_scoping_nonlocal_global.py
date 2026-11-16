"""CASE: global / nonlocal rebinding, class scope vs module scope, closures in loops.
WHY: assignments in inner scopes must reach the outer variable.
EXPECTED (ground truth from a traced run; caller -> callee):
  <lambda>@71 -> pick
  <module> -> <lambda>@71
  <module> -> K.m
  <module> -> outer
  <module> -> run
  <module> -> set_handler
  K.m -> a
  outer -> b
  outer -> outer.inner
  run -> a
  run -> b
"""


def a():
    return 1


def b():
    return 2


def pick(i):
    return i


handler = a


def set_handler():
    global handler
    handler = b


def run():
    return handler()


run()
set_handler()
run()


def outer():
    f = a

    def inner():
        nonlocal f
        f = b
    inner()
    return f()


outer()
x = a


class K:
    x = b  # class scope, invisible to methods

    def m(self):
        return x()  # module-level x


K().m()
funcs = []
for i in range(2):
    funcs.append(lambda: pick(i))
[fn() for fn in funcs]
