"""Multi-file programs: loading imported modules, resolving their names, and module values.

Each test writes a small project under `tmp_path` and analyses one of its files; edges are
compared in the "module" id style (`module.qualname`).
"""
from __future__ import annotations

import ast
import json
import textwrap
from collections.abc import Sequence
from pathlib import Path

import pytest

from callme import analyze_file, analyze_files, load_state, save_state
from callme.__main__ import main as cli
from callme.frontend import ModuleInfo, absolute_module, module_all, parse_file
from callme.model import token_label

Edge = tuple[str, str]


def make(root: Path, files: dict[str, str]) -> Path:
    """Write `files` (relative path -> source) under `root` and return `root`."""
    for rel, src in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src), encoding="utf-8")
    return root


def edges(path: Path, *, search_path: Sequence[Path] = (),
          include_unreachable: bool = False) -> set[Edge]:
    """Return the function-level edges of the program whose entry is `path`."""
    cg = analyze_file(path, id_style="module", search_path=search_path,
                      include_unreachable=include_unreachable)
    return set(cg.function_edges())


def test_entry_inside_a_package_is_named_from_the_package_root(tmp_path: Path) -> None:
    make(tmp_path, {
        "app/__init__.py": "",
        "app/cli.py": "from .core import run\nrun()\n",
        "app/core.py": "def run(): pass\n",
    })
    prog = parse_file(tmp_path / "app" / "cli.py")
    assert prog.module.module == "app.cli"
    assert set(prog.modules) == {"app", "app.cli", "app.core"}
    assert edges(tmp_path / "app" / "cli.py") == {("app.cli.<module>", "app.core.run")}


def test_search_path_adds_import_roots(tmp_path: Path) -> None:
    make(tmp_path, {"scripts/run.py": "import lib\nlib.f()\n", "src/lib.py": "def f(): pass\n"})
    entry = tmp_path / "scripts" / "run.py"
    assert edges(entry) == set()
    assert edges(entry, search_path=[tmp_path / "src"]) == {("run.<module>", "lib.f")}


def test_namespace_packages_and_submodules_as_attributes(tmp_path: Path) -> None:
    make(tmp_path, {
        "ns/sub/mod.py": "def f(): pass\n",
        "pkg/__init__.py": "",
        "pkg/inner.py": "def g(): pass\n",
        "main.py": """\
            import ns.sub.mod
            import pkg.inner
            import pkg
            ns.sub.mod.f()
            pkg.inner.g()
            """,
    })
    prog = parse_file(tmp_path / "main.py")
    assert prog.modules["ns"].file is None and prog.modules["ns.sub"].func is None
    assert edges(tmp_path / "main.py") == {("main.<module>", "ns.sub.mod.f"),
                                           ("main.<module>", "pkg.inner.g")}


def test_star_imports_use_all_then_public_names_and_chain(tmp_path: Path) -> None:
    make(tmp_path, {
        "lib.py": "__all__ = ['a']\ndef a(): pass\ndef b(): pass\n",
        "mid.py": "from lib import *\ndef c(): pass\ndef _d(): pass\n",
        "main.py": "from mid import *\na()\nb()\nc()\n",
    })
    prog = parse_file(tmp_path / "main.py")
    assert prog.modules["lib"].exports == {"a"}
    assert prog.modules["mid"].exports == {"a", "c"}
    assert edges(tmp_path / "main.py") == {("main.<module>", "lib.a"), ("main.<module>", "mid.c")}


def test_module_all_reads_literal_lists_only() -> None:
    body = ast.parse("__all__ = ['a']\n__all__ += ['b']\n__all__.append('c')\n").body
    assert module_all(body) == {"a", "b", "c"}
    assert module_all(ast.parse("__all__ = names()\n").body) is None
    assert module_all(ast.parse("x = 1\n").body) is None


def test_relative_imports_count_from_the_package() -> None:
    mod = ModuleInfo("a.b", "a/b.py", None, False)
    pkg = ModuleInfo("a.b", "a/b/__init__.py", None, True)
    assert absolute_module(mod, 1, "c") == "a.c"
    assert absolute_module(pkg, 1, "c") == "a.b.c"
    assert absolute_module(mod, 1, None) == "a"
    assert absolute_module(mod, 2, None) is None  # beyond the top-level package
    assert absolute_module(ModuleInfo("main", "main.py", None, False), 1, "x") is None
    assert absolute_module(mod, 0, "os.path") == "os.path"


def test_standard_library_names_are_not_project_modules(tmp_path: Path) -> None:
    (tmp_path / "json").mkdir()  # a data directory: no namespace package shadows the stdlib
    make(tmp_path, {"sys.py": "def f(): pass\n", "main.py": "import json\nimport sys\nsys.f()\n"})
    assert set(parse_file(tmp_path / "main.py").modules) == {"main"}
    assert edges(tmp_path / "main.py") == set()


def test_a_project_module_wins_over_a_stub_module(tmp_path: Path) -> None:
    make(tmp_path, {
        "functools.py": "def reduce(f, xs):\n    return f(xs, xs)\n",
        "main.py": "import functools\ndef add(a, b): return a\nfunctools.reduce(add, [1])\n",
    })
    assert edges(tmp_path / "main.py") == {("main.<module>", "functools.reduce"),
                                           ("functools.reduce", "main.add")}


def test_globals_and_attributes_written_from_another_module(tmp_path: Path) -> None:
    make(tmp_path, {
        "lib.py": """\
            def a(): pass
            def b(): pass
            hook = a
            def set_hook(f):
                global hook
                hook = f
            def fire():
                hook()
            def call_b():
                b()
            """,
        "main.py": """\
            import lib
            def mine(): pass
            def other(): pass
            lib.set_hook(mine)
            lib.fire()
            lib.b = other
            lib.call_b()
            """,
    })
    got = edges(tmp_path / "main.py")
    assert {("lib.fire", "lib.a"), ("lib.fire", "main.mine"),
            ("lib.call_b", "lib.b"), ("lib.call_b", "main.other")} <= got


def test_a_module_object_flows_into_contexts(tmp_path: Path) -> None:
    make(tmp_path, {"lib.py": "def f(): pass\n",
                    "main.py": "import lib\ndef use(m):\n    m.f()\nuse(lib)\n"})
    cg = analyze_file(tmp_path / "main.py", id_style="module")
    assert ("main.use", "lib.f") in cg.function_edges()
    contexts = [{p: sorted(token_label(t) for t in ts) for p, ts in n.ctx.params}
                for n in cg.nodes if n.func.qualname == "use"]
    assert contexts == [{"m": ["<module lib>"]}]


def test_imports_run_module_bodies_without_edges(tmp_path: Path) -> None:
    make(tmp_path, {"lazy.py": "def f(): pass\nf()\n",
                    "main.py": "def dead():\n    import lazy\n"})
    assert edges(tmp_path / "main.py") == set()  # `dead` never runs, so neither does lazy.py
    assert edges(tmp_path / "main.py", include_unreachable=True) == {("lazy.<module>", "lazy.f")}


def test_dynamic_imports_return_every_loaded_module(tmp_path: Path) -> None:
    make(tmp_path, {
        "lib.py": "def work(): pass\n",
        "plug.py": "def work(): pass\n",
        "main.py": """\
            import importlib
            def load(name):
                return importlib.import_module(name)
            def raw(name):
                return __import__(name)
            importlib.import_module("plug")  # a literal name is followed like an import
            load("lib").work()
            raw("lib").work()
            """,
    })
    assert set(parse_file(tmp_path / "main.py").modules) == {"main", "plug"}  # lib is never named
    make(tmp_path, {"main.py": "import lib\n" + (tmp_path / "main.py").read_text()})
    assert edges(tmp_path / "main.py") == {("main.<module>", "main.load"),
                                           ("main.<module>", "main.raw"),
                                           ("main.<module>", "lib.work"),
                                           ("main.<module>", "plug.work")}


def test_several_entries_are_all_roots(tmp_path: Path) -> None:
    make(tmp_path, {"a.py": "def f(): pass\nf()\n", "b.py": "def g(): pass\ng()\n"})
    cg = analyze_files([tmp_path / "a.py", tmp_path / "b.py"], id_style="module")
    assert len(cg.prog.entries) == 2
    assert set(cg.function_edges()) == {("a.<module>", "a.f"), ("b.<module>", "b.g")}


def test_two_entries_with_one_module_name_are_rejected(tmp_path: Path) -> None:
    make(tmp_path, {"x/main.py": "", "y/main.py": ""})
    with pytest.raises(ValueError, match="both be module 'main'"):
        analyze_files([tmp_path / "x" / "main.py", tmp_path / "y" / "main.py"])


def test_cli_with_two_files_and_a_search_path(tmp_path: Path) -> None:
    make(tmp_path, {"a.py": "import lib\nlib.f()\n", "b.py": "import lib\nlib.g()\n",
                    "src/lib.py": "def f(): pass\ndef g(): pass\n"})
    out = tmp_path / "out.json"
    assert cli([str(tmp_path / "a.py"), str(tmp_path / "b.py"), "--path", str(tmp_path / "src"),
                "--id-style", "module", "-o", str(out)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert [m["name"] for m in doc["modules"]] == ["a", "b", "lib"]
    assert {(e["caller"], e["callee"]) for e in doc["edges"]} == {("a.<module>", "lib.f"),
                                                                 ("b.<module>", "lib.g")}


def test_saved_state_with_modules_resumes_until_a_module_changes(tmp_path: Path) -> None:
    make(tmp_path, {"lib.py": "def f(): pass\n",
                    "main.py": "import lib\ndef use(m):\n    m.f()\nuse(lib)\n"})
    cg = analyze_file(tmp_path / "main.py")
    saved = tmp_path / "state.json"
    save_state(cg.state, saved)
    assert load_state(parse_file(tmp_path / "main.py"), saved).same_result(cg.state)
    (tmp_path / "lib.py").write_text("def f(): return 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="different program"):
        load_state(parse_file(tmp_path / "main.py"), saved)
