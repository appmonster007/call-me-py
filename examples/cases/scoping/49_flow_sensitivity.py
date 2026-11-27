"""CASE: flow sensitivity: which binding of a name reaches each call (rebinding, branches, loops, break, except).
WHY: a flow-insensitive analysis unions every binding of a local, so a stale binding leaks into run().
     The other functions check that strong updates stay sound across joins, loops and exceptions.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> a
  <module> -> b
  <module> -> branches
  <module> -> comp
  <module> -> early
  <module> -> guarded
  <module> -> loop
  <module> -> set_q
  <module> -> straight
  branches -> a
  branches -> b
  comp -> a
  early -> b
  guarded -> b
  loop -> a
  loop -> b
  run -> b
  straight -> run
STATIC_ONLY (paths this run does not take; no iteration, no break, raise before m = b):
  early -> a
  early -> c
  guarded -> a
SITES (exact callees of the calls on one line; caller and line -> callee):
  run:52 -> b
  branches:66 -> a
  branches:66 -> b
  loop:72 -> a
  loop:72 -> b
  comp:98 -> a
NOTE: q is written by set_q (global), so both q() calls resolve to {a, b}: inner-scope writes stay
      flow-insensitive. The edges are still exact (<module> calls a and b).
"""


def a():
    return "a"


def b():
    return "b"


def c():
    return "c"


def run(cb):
    return cb()


def straight():  # rebinding: only b reaches run
    f = a
    f = b
    return run(f)


def branches(flag):  # if/else: both bindings reach the call
    if flag:
        g = a
    else:
        g = b
    return g()


def loop(n):  # loop-carried: the second iteration sees h = b
    h = a
    for _ in range(n):
        h()
        h = b


def early(xs):  # break: the state at the break reaches the loop exit
    k = a
    for x in xs:
        k = b
        if x:
            break
        k = c
    return k()


def guarded():  # except: the handler sees the state where the exception was raised
    m = a
    try:
        m = b
        raise ValueError
    except ValueError:
        return m()


def comp():  # the comprehension's p is its own variable
    p = a
    [p for p in [b]]
    return p()


def set_q():
    global q
    q = b


straight()
branches(True)
branches(False)
loop(2)
early([True])
guarded()
comp()
q = a
q()
set_q()
q()  # global written by set_q: the strong update q = a must not hide it
