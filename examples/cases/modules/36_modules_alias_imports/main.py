"""CASE: aliased imports, re-exports through __init__, relative imports, attribute access on a package.
WHY: names at the call site (go, E, h, helpers, tw, pkg.core) must be mapped back to definitions.
STUBS (external names a collector should keep, narrowed to what is used):
  os.path.join
  json.dumps
EXPECTED (ground truth from a traced run; caller -> callee):
  main.<module> -> main.main
  main.main -> pkg.core.Engine.start
  main.main -> pkg.core.run
  main.main -> pkg.util.helpers.double
  main.main -> pkg.util.helpers.twice
  pkg.core.Engine.start -> pkg.core.run
  pkg.core.run -> pkg.util.helpers.double
  pkg.core.run -> pkg.util.helpers.twice
  pkg.util.helpers.twice -> pkg.util.helpers.double
"""
import json as js
import os.path as osp

import pkg
import pkg.util.helpers as h
from pkg import run as go, Engine as E
from pkg.util import helpers
from pkg.util.helpers import twice as tw


def main():
    go(1)
    E().start()
    h.double(2)
    helpers.double(3)
    tw(4)
    pkg.core.run(5)
    osp.join("a", "b")
    js.dumps({})


main()
