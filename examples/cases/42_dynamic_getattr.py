"""CASE: getattr / globals() / vars() / operator.methodcaller / attrgetter dispatch by string.
WHY: the callee name is computed at runtime; a static analysis can only over-approximate or give up.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> H.on_a
  <module> -> H.on_b
  <module> -> f1
  <module> -> f2
"""
import operator


class H:
    def on_a(self):
        return 1

    def on_b(self):
        return 2


def f1():
    return 1


def f2():
    return 2


h = H()
for ev in ("a", "b"):
    getattr(h, "on_" + ev)()
globals()["f1"]()
{"x": f2}["x"]()
vars(H)["on_a"](h)
operator.methodcaller("on_b")(h)
operator.attrgetter("on_a")(h)()
