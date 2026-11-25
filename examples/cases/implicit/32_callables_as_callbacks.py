"""CASE: classes, instances with __call__, property fget and defaultdict factories used as callbacks.
WHY: the callback is not a plain function; constructors and __call__ are the real callees.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> Box.__init__
  <module> -> Key.__call__
  <module> -> fget
  <module> -> make
"""
import collections
import functools


class Box:
    def __init__(self, v):
        self.v = v


class Key:
    def __call__(self, x):
        return x


def make():
    return []


def fget(self):
    return 1


class P:
    v = property(fget)


d = collections.defaultdict(make)
d["k"]
list(map(Box, [1]))
sorted([2, 1], key=Key())
P().v
partial_cls = functools.partial(Box, 1)
partial_cls()
