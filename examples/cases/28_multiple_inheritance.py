"""CASE: diamond inheritance with cooperative super(), inherited method through a mixin.
WHY: super() follows the receiver's MRO, not the lexical base: Left.hello -> Right.hello.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> Diamond.hello
  <module> -> Mixin.extra
  <module> -> Right.hello
  Diamond.hello -> Left.hello
  Left.hello -> Right.hello
  Mixin.extra -> Right.hello
  Right.hello -> Base.hello
"""


class Base:
    def hello(self):
        return "base"


class Left(Base):
    def hello(self):
        return "L" + super().hello()


class Right(Base):
    def hello(self):
        return "R" + super().hello()


class Diamond(Left, Right):
    def hello(self):
        return "D" + super().hello()


class OnlyRight(Right):
    pass


class Mixin:
    def extra(self):
        return self.hello()


class Combo(Mixin, Right):
    pass


Diamond().hello()
OnlyRight().hello()
Combo().extra()
