"""CASE: calls into the standard library through aliases; a parameter that shadows an imported module name.
WHY: external callees are stubs; only names that are actually used should become stubs.
STUBS (external names a collector should keep, narrowed to what is used):
  os.path.join
  json.dumps
  itertools.chain
  math.sqrt
EXPECTED (ground truth from a traced run; caller -> callee):
  main.<module> -> main.run
  main.run -> main.local
  main.run -> main.shadow
  main.shadow -> main.D.dumps
"""
import json
import os.path as osp
from itertools import chain as ch
from math import sqrt as rt


class D:
    def dumps(self, v):
        return v


def local(x):
    return rt(x)


def shadow(json):
    return json.dumps(1)  # parameter, not the json module


def run():
    osp.join("a", "b")
    json.dumps({})
    list(ch([1], [2]))
    local(4)
    shadow(D())


run()
