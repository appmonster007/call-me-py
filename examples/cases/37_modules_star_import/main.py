"""CASE: star imports from project modules, with and without __all__ (a star import also re-exports imported names).
WHY: calls to bare names can only be resolved through the star-imported module's export list.
STUBS (external names a collector should keep, narrowed to what is used):
  os.path.join
EXPECTED (ground truth from a traced run; caller -> callee):
  lib_all.Thing.go -> lib_all._private
  main.<module> -> main.main
  main.main -> lib_all.Thing.go
  main.main -> lib_all.public
  main.main -> lib_noall.exported
"""
from lib_all import *
from lib_noall import *


def main():
    public()
    Thing().go()
    exported()
    join("a", "b")  # comes from lib_noall's own `from os.path import join`


main()
