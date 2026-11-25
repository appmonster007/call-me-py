"""CASE: nested namespace packages (no __init__.py), relative import of a sibling module, dotted module aliasing.
WHY: module names come from the directory layout.
EXPECTED (ground truth from a traced run; caller -> callee):
  main.<module> -> main.main
  main.main -> outer.inner.leaf.leaf
  main.main -> outer.inner.mid.mid
  outer.inner.mid.mid -> outer.inner.leaf.leaf
"""
import outer.inner.leaf as L
from outer.inner import mid as m


def main():
    m.mid()
    L.leaf()


main()
