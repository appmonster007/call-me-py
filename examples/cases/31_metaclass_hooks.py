"""CASE: metaclass __new__/__init__/__call__, __init_subclass__, class decorator.
WHY: defining or instantiating a class runs user code with no explicit call.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> Base.__init_subclass__
  <module> -> Meta.__call__
  <module> -> Meta.__init__
  <module> -> Meta.__new__
  <module> -> deco
  Base.__init_subclass__ -> hook
  Meta.__call__ -> A.__init__
  Meta.__init__ -> register
  deco -> tag
"""


def register(n):
    return n


def hook(c):
    return c


def tag():
    return 1


def deco(cls):
    cls.tagged = tag()
    return cls


class Meta(type):
    def __new__(mcls, name, bases, ns):
        return super().__new__(mcls, name, bases, ns)

    def __init__(cls, name, bases, ns):
        super().__init__(name, bases, ns)
        cls.registered = register(name)

    def __call__(cls, *a):
        return super().__call__(*a)


class Base:
    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        hook(cls)


class A(metaclass=Meta):
    def __init__(self):
        self.v = 1


class B(Base):
    pass


@deco
class C:
    pass


A()
