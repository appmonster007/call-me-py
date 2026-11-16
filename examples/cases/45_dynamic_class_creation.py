"""CASE: classes created with type(), setattr on a locally defined class, methods injected via a namespace dict.
WHY: methods are attached to classes by data, not by def statements inside the class body.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> Base.base
  <module> -> hello
  <module> -> make_class
  hello -> greet
"""


def greet():
    return "hi"


def hello(self):
    return greet()


class Base:
    def base(self):
        return 1


T = type("T", (Base,), {"hello": hello})
t = T()
t.hello()
t.base()


def make_class(fn):
    class Dyn(Base):
        pass
    setattr(Dyn, "run", fn)
    return Dyn


make_class(hello)().run()
ns = {}
ns.update(hello=hello)
Y = type("Y", (), ns)
Y().hello()
