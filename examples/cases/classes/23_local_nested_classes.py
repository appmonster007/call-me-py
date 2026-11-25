"""CASE: class defined inside a function, nested class, instantiating a nested class via self.
WHY: qualified names of methods include enclosing scopes.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> Outer.Inner.go
  <module> -> Outer.build
  <module> -> make_cls
  <module> -> make_cls.Local.hi
  Outer.Inner.go -> Outer.top
  make_cls.Local.hi -> helper
"""


def helper():
    return 1


def make_cls():
    class Local:
        def hi(self):
            return helper()
    return Local


class Outer:
    class Inner:
        def go(self):
            return Outer.top()

    @staticmethod
    def top():
        return 0

    def build(self):
        return self.Inner()


L = make_cls()
L().hi()
Outer().build().go()
Outer.Inner().go()
