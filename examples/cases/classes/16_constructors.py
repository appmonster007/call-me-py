"""CASE: __new__, __init__, classmethod factories, dataclass __post_init__, class used as a callable.
WHY: calling a class implicitly calls several methods.
NOTE: the dataclass-generated __init__ is not in the source; __post_init__ is called "from" its caller.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> Box.__init__
  <module> -> D.__post_init__
  <module> -> P.__init__
  <module> -> P.__new__
  <module> -> P.from_str
  <module> -> Q.__init__
  D.__post_init__ -> D.check
  P.__init__ -> P.norm
  P.from_str -> P.__init__
  P.from_str -> P.__new__
  Q.__init__ -> P.__init__
"""
from dataclasses import dataclass


class P:
    def __new__(cls, *a):
        return super().__new__(cls)

    def __init__(self, x):
        self.x = self.norm(x)

    def norm(self, x):
        return x

    @classmethod
    def from_str(cls, s):
        return cls(int(s))


class Q(P):
    def __init__(self, x):
        super().__init__(x)


@dataclass
class D:
    x: int

    def __post_init__(self):
        self.check()

    def check(self):
        pass


class Box:
    def __init__(self, v):
        self.v = v


P(1)
Q(2)
P.from_str("3")
D(1)
list(map(Box, [1, 2]))
factory = Box
factory(3)
