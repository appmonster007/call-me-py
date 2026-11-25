"""CASE: eval / exec / compile of source strings that contain calls.
WHY: the call sites do not exist in the AST.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> f
  <module> -> g
"""


def f():
    return 1


def g():
    return 2


eval("f()")
exec("g()")
code = compile("f()", "<s>", "eval")
eval(code)
