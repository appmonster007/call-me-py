"""CASE: registration decorators filling a dict, then dispatch by name; functools.singledispatch.
WHY: decorators run code at import time and store callees in a registry that is read later.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> dispatch
  <module> -> register
  <module> -> register.deco
  <module> -> show
  <module> -> show_int
  <module> -> show_str
  dispatch -> handle_a
  dispatch -> handle_b
"""
import functools

REG = {}


def register(name):
    def deco(fn):
        REG[name] = fn
        return fn
    return deco


@register("a")
def handle_a():
    return 1


@register("b")
def handle_b():
    return 2


def dispatch(name):
    return REG[name]()


@functools.singledispatch
def show(x):
    return "obj"


@show.register(int)
def show_int(x):
    return "int"


@show.register(str)
def show_str(x):
    return "str"


dispatch("a")
dispatch("b")
show(1)
show("s")
show(1.0)
