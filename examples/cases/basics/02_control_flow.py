"""CASE: calls inside if/else, ternary, try/except/finally, for/while/else, with.
WHY: flow-insensitive analyses see all branches; run() exercises both branches.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> run
  run -> a
  run -> b
  run -> boom
  run -> c
  run -> cleanup
  run -> handler
"""


def a():
    return 1


def b():
    return 2


def c():
    return 3


def cleanup():
    pass


def boom():
    raise ValueError


def handler():
    return 0


def run(flag):
    a() if flag else b()
    if flag:
        c()
    else:
        b()
    try:
        boom()
    except ValueError:
        handler()
    finally:
        cleanup()
    for _ in range(1):
        a()
    n = 1
    while n:
        c()
        n -= 1
    else:
        b()
    with open(__file__):
        a()


run(True)
run(False)
