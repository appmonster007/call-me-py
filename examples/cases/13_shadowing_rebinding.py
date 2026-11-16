"""CASE: name rebinding and shadowing (module name, parameter, local def, builtin, user-defined map).
WHY: resolving a name needs scopes; a flow-insensitive analysis sees every binding of f at every call.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> a
  <module> -> b
  <module> -> local_def
  <module> -> map
  <module> -> shadow_builtin
  <module> -> with_param
  local_def -> local_def.a
  map -> ident
  shadow_builtin -> shadow_builtin.len
  with_param -> b
"""


def a():
    return "a"


def b():
    return "b"


def ident(x):
    return x


f = a
f()
f = b
f()


def with_param(a):
    return a()  # parameter shadows global a


def local_def():
    def a():
        return "local"
    return a()  # local def shadows global a


def shadow_builtin():
    def len(x):
        return 0
    return len([1])


with_param(b)
local_def()
shadow_builtin()


def map(fn, xs):  # shadows the builtin
    return [fn(x) for x in xs]


map(ident, [1])
