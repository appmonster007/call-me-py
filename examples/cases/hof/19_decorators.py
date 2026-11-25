"""CASE: function decorators (plain, with arguments, stacked), class decorator object, method decorator, registry decorator.
WHY: the name at the call site is bound to the wrapper; the original is called through a free variable.
NOTE: decorator application is itself a call made at definition time.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> CountCalls.__call__
  <module> -> CountCalls.__init__
  <module> -> plugin
  <module> -> register
  <module> -> repeat
  <module> -> repeat.deco
  <module> -> repeat.deco.wrapper
  <module> -> trace
  <module> -> trace.wrapper
  CountCalls.__call__ -> counted
  repeat.deco.wrapper -> shout
  repeat.deco.wrapper -> stacked
  trace.wrapper -> K.m
  trace.wrapper -> hello
  trace.wrapper -> repeat.deco.wrapper
"""
import functools

REG = []


def trace(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return fn(*a, **k)
    return wrapper


def repeat(n):
    def deco(fn):
        def wrapper(*a):
            for _ in range(n):
                fn(*a)
        return wrapper
    return deco


def register(fn):
    REG.append(fn)
    return fn


class CountCalls:
    def __init__(self, fn):
        self.fn = fn

    def __call__(self, *a):
        return self.fn(*a)


@trace
def hello():
    return "hi"


@repeat(2)
def shout(s):
    return s


@trace
@repeat(1)
def stacked():
    return 1


@register
def plugin():
    return 1


@CountCalls
def counted(x):
    return x


class K:
    @trace
    def m(self):
        return 1


hello()
shout("x")
stacked()
plugin()
counted(1)
K().m()
for f in REG:
    f()
