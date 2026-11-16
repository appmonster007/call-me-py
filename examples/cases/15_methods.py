"""CASE: instance, class and static methods; calls via self, cls, instance, class name and bound-method values.
WHY: method resolution needs the receiver's class.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> A.__init__
  <module> -> A.inst
  <module> -> A.stat
  A.__init__ -> A.base
  A.cls_m -> A.make
  A.helper -> A.cls_m
  A.helper -> A.stat
  A.inst -> A.base
  A.inst -> A.helper
  A.make -> A.__init__
  A.stat -> util
"""


def util():
    return 0


class A:
    def __init__(self):
        self.v = self.base()

    def base(self):
        return 1

    def inst(self):
        return self.base() + self.helper()

    def helper(self):
        self.cls_m()
        return self.stat()

    @classmethod
    def cls_m(cls):
        return cls.make()

    @classmethod
    def make(cls):
        return cls()

    @staticmethod
    def stat():
        return util()


a = A()
a.inst()
m = a.inst
m()
A.inst(a)
f = A.stat
f()
