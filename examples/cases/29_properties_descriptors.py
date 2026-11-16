"""CASE: property getter/setter, descriptor __get__/__set__, __getattr__ fallback, __setattr__.
WHY: plain attribute syntax performs calls (no call expression in the source).
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> Desc.__get__
  <module> -> Holder.__getattr__
  <module> -> Holder.__setattr__
  <module> -> Temp.__init__
  <module> -> Temp.c
  <module> -> Temp.f
  Desc.__get__ -> load
  Desc.__set__ -> store
  Holder.__getattr__ -> fallback
  Holder.__setattr__ -> Desc.__set__
  Temp.c -> Temp.check
  Temp.f -> Temp.c
"""


def load():
    return 1


def store(v):
    pass


def fallback(n):
    return n


class Temp:
    def __init__(self):
        self._c = 0

    @property
    def c(self):
        return self._c

    @c.setter
    def c(self, v):
        self._c = self.check(v)

    def check(self, v):
        return v

    @property
    def f(self):
        return self.c * 9 / 5 + 32


class Desc:
    def __get__(self, obj, owner):
        return load()

    def __set__(self, obj, v):
        store(v)


class Holder:
    d = Desc()

    def __getattr__(self, name):
        return fallback(name)

    def __setattr__(self, name, v):
        object.__setattr__(self, name, v)


t = Temp()
t.c = 5
t.c
t.f
h = Holder()
h.d
h.d = 1
h.missing
