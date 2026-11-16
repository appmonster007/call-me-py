"""CASE: plugins loaded with importlib.import_module from a list of module names.
WHY: the imported modules are only known from string data.
EXPECTED (ground truth from a traced run; caller -> callee):
  main.<module> -> main.main
  main.main -> main.load_all
  main.main -> plugin_a.extra
  main.main -> plugin_a.run
  main.main -> plugin_b.run
  plugin_a.run -> plugin_a.helper
"""
import importlib

PLUGINS = ["plugin_a", "plugin_b"]


def load_all():
    return [importlib.import_module(n) for n in PLUGINS]


def main():
    for mod in load_all():
        mod.run()
    getattr(importlib.import_module("plugin_a"), "extra")()


main()
