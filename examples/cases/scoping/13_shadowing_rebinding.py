"""CASE: name rebinding and shadowing (module name, parameter, local def, builtin, user-defined map).
WHY: resolving a name needs scopes and program order: each f() sees only the binding that reaches it.
     The function edges cannot tell (both <module> -> a and <module> -> b are real); SITES can.
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
SITES (exact callees of the calls on one line; caller and line -> callee):
  <module>:38 -> a
  <module>:40 -> b
  with_param:44 -> b
  local_def:50 -> local_def.a
  shadow_builtin:56 -> shadow_builtin.len
  map:65 -> ident
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
