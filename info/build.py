"""Generate info/generated/data.js from the real analyzer, or check that it is current.

    python info/build.py           # regenerate
    python info/build.py --check   # exit 1 if data.js is stale or a code-ref is broken

The pages never hard-code analyzer results: everything they animate comes from here.

    refs      every `sym="path::Qual.name"` a page uses -> file, lines and source snippet
    examples  each program in EXAMPLES (info/examples/), analysed per max_contexts variant
    cases     ground truth vs. found edges for the test cases in CASES (chapter 9)
    sources   the callme package itself, for the in-browser playground
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "info"
OUT = DOCS / "generated" / "data.js"
sys.path.insert(0, str(ROOT))

from callme import analyze_file  # noqa: E402
from callme.analysis import State, solve  # noqa: E402
from callme.frontend import Program, build_program, call_sites  # noqa: E402
from callme.model import (  # noqa: E402
    BoundMethod,
    Builtin,
    ClassMethod,
    ClassObj,
    Closure,
    Edge,
    FuncId,
    Instance,
    Node,
    Site,
    StaticMethod,
    Super,
    Token,
    Vals,
    token_label,
)
from callme.output import CallGraph, build_callgraph, to_dict, to_dot  # noqa: E402
from tests.test_examples import ALLOWED_EXTRA, parse_blocks  # noqa: E402  (the test suite's own parser)

REPO = {"url": "https://github.com/appmonster007/call-me-py", "branch": "main"}
STYLE = "short"  # id style of every name shown in the docs
MAX_SNIPPET = 60  # lines of source shown for one code-ref


@dataclass(frozen=True)
class Example:
    """A program from info/examples/ that a chapter animates."""

    file: str
    max_contexts: tuple[int, ...] = (16,)  # one analysis per value (chapter 3 compares them)
    trace: bool = False  # record what each solver round adds (chapter 4)


EXAMPLES: dict[str, Example] = {
    "basics": Example("basics.py", trace=True),
    "mapping": Example("mapping.py"),
    "contexts": Example("contexts.py", max_contexts=(0, 1, 16)),
    "closures": Example("closures.py"),
    "heap": Example("heap.py"),
    "objects": Example("objects.py"),
    "dunders": Example("dunders.py"),
    "stubs": Example("stubs.py"),
}
CASES = (  # examples/cases/<family>/<case>.py shown in chapter 9
    "basics/25_dead_conditional",
    "hof/08_function_as_argument",
    "hof/19_decorators",
)
JSON = dict[str, object]


# ---------------------------------------------------------------- tokens and nodes
def base_name(t: Token) -> str:
    """The function or class a token stands for: pages color chips by it."""
    if isinstance(t, Closure):
        return t.func.render(STYLE)
    if isinstance(t, (BoundMethod, ClassMethod, StaticMethod)):
        return base_name(t.func)
    if isinstance(t, (ClassObj, Instance)):
        return t.cls.render(STYLE)
    if isinstance(t, Super):
        return t.start.render(STYLE)
    if isinstance(t, Builtin) and t.name.startswith("<partial") and len(t.bound[0]) == 1:
        return base_name(next(iter(t.bound[0])))  # a partial takes the color of what it wraps
    return token_label(t, STYLE)


def tok(t: Token, labels: dict[Node, str]) -> JSON:
    """A token as the pages see it: kind (drives the chip shape), label, and what it stands for."""
    out: JSON = {"kind": type(t).__name__, "label": token_label(t, STYLE), "name": base_name(t)}
    if isinstance(t, BoundMethod):
        out["receiver"] = token_label(t.receiver, STYLE)
    if isinstance(t, Closure) and t.parent is not None and t.parent in labels:
        out["parent"] = labels[t.parent]  # chapter 5 draws the tether
    if isinstance(t, Builtin) and t.bound:  # a partial shows what it carries
        out["bound"] = [toks(v, labels) for v in t.bound]
    return out


def toks(vals: Vals, labels: dict[Node, str]) -> list[JSON]:
    return sorted((tok(t, labels) for t in vals), key=lambda d: str(d["label"]))


def env_json(env: dict[str, Vals], labels: dict[Node, str]) -> JSON:
    return {name: toks(v, labels) for name, v in sorted(env.items()) if v}


def site_json(s: Site) -> JSON:
    return {"line": s.line, "col": s.col, "text": s.text, "kind": s.kind}


def edge_json(e: Edge, labels: dict[Node, str]) -> JSON:
    return {"caller": labels[e.caller], "callee": labels[e.callee], "site": site_json(e.site)}


def node_json(n: Node, state: State, labels: dict[Node, str]) -> JSON:
    return {
        "id": labels[n],
        "function": n.func.render(STYLE),
        "context": {p: toks(v, labels) for p, v in n.ctx.params},
        "merged": n.ctx.merged,
        "parent": labels.get(n.ctx.parent) if n.ctx.parent else None,
        "env": env_json(state.envs.get(n, {}), labels),
        "ret": toks(state.ret.get(n, frozenset()), labels),
    }


# ---------------------------------------------------------------- solver trace
@dataclass(frozen=True)
class Snapshot:
    """What the solver knew at the end of one round (copied: State mutates in place)."""

    nodes: frozenset[Node]
    edges: frozenset[Edge]
    ret: dict[Node, Vals]
    envs: dict[Node, dict[str, Vals]]
    changed: bool

    @classmethod
    def of(cls, state: State) -> Snapshot:
        return cls(frozenset(state.order), frozenset(state.edges), dict(state.ret),
                   {n: dict(e) for n, e in state.envs.items()}, state.changed)


def rounds_json(snaps: list[Snapshot], start: frozenset[Node], labels: dict[Node, str]) -> list[JSON]:
    """Per round: only what it added (nodes, edges, return and env tokens).

    `start` are the nodes registered before round 0 (the module body).
    """
    out: list[JSON] = []
    empty: frozenset[Token] = frozenset()
    prev = Snapshot(start, frozenset(), {}, {}, False)
    for i, s in enumerate(snaps):
        envs: JSON = {}
        for n, env in s.envs.items():
            old = prev.envs.get(n, {})
            new = {k: v - old.get(k, empty) for k, v in env.items()}
            if any(new.values()):
                envs[labels[n]] = env_json(new, labels)
        out.append({
            "round": i,
            "changed": s.changed,
            "nodes": sorted(labels[n] for n in s.nodes - prev.nodes),
            "edges": sorted((edge_json(e, labels) for e in s.edges - prev.edges),
                            key=lambda d: json.dumps(d, sort_keys=True)),
            "ret": {labels[n]: toks(v - prev.ret.get(n, empty), labels)
                    for n, v in s.ret.items() if v - prev.ret.get(n, empty)},
            "env": envs,
        })
        prev = s
    return out


# ---------------------------------------------------------------- examples
def program_json(prog: Program, n_lines: int) -> JSON:
    """Step 1 (frontend/): every function with its ids, scope, locals and call sites."""
    def end(f: FuncId) -> int:  # last line of the definition (the module spans the file)
        return n_lines if f == prog.module else getattr(prog.funcs[f].node, "end_lineno", f.lineno)

    return {
        "functions": [{
            "id": i.id.render(STYLE),
            "ids": {s: i.id.render(s) for s in ("full", "module", "short")},
            "kind": i.kind,
            "line": i.id.lineno,
            "end": end(i.id),
            "parent": i.parent.render(STYLE) if i.parent else None,
            "params": [*i.sig.pos, *i.sig.kwonly, *filter(None, [i.sig.vararg, i.sig.kwarg])],
            "locals": sorted(i.locals),
            "sites": [site_json(s) for s in call_sites(prog, i.id)],
        } for i in prog.funcs.values()],
        "classes": [{"id": c.id.render(STYLE), "line": c.id.lineno,
                     "parent": c.parent.render(STYLE)} for c in prog.classes.values()],
    }


def run_json(prog: Program, max_contexts: int, trace: bool) -> JSON:
    snaps: list[Snapshot] = []
    state = solve(prog, max_contexts=max_contexts,
                  on_round=(lambda _r, s: snaps.append(Snapshot.of(s))) if trace else None)
    cg: CallGraph = build_callgraph(prog, state, STYLE)
    labels = cg.labels
    graph = to_dict(cg)
    out: JSON = {
        "nodes": [node_json(n, state, labels) for n in cg.nodes],
        "edges": graph["edges"],
        "context_edges": graph["context_edges"],
        "sites": graph["sites"],
        "heap": {token_label(b, STYLE): toks(v, labels)
                 for b, v in sorted(state.heap.items(), key=lambda p: token_label(p[0]))},
        "classes": [{"id": c.render(STYLE), "mro": [m.render(STYLE) for m in state.mro(c)],
                     "attrs": env_json(state.class_attrs.get(c, {}), labels)}
                    for c in prog.classes],
        "dot": to_dot(cg),
    }
    if trace:
        out["rounds"] = rounds_json(snaps, frozenset(state.order[:1]), labels)
    return out


def example_json(ex: Example) -> JSON:
    path = DOCS / "examples" / ex.file
    source = path.read_text(encoding="utf-8")
    prog = build_program(source, ex.file)
    return {
        "file": f"info/examples/{ex.file}",
        "source": source,
        "program": program_json(prog, len(source.splitlines())),
        "runs": {str(k): run_json(build_program(source, ex.file), k, ex.trace)
                 for k in ex.max_contexts},
    }


def case_json(case: str) -> JSON:
    path = ROOT / "examples" / "cases" / f"{case}.py"
    blocks = parse_blocks(ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or "")
    found = analyze_file(path.relative_to(ROOT), id_style=STYLE).function_edges()

    def edges(es: set[tuple[str, str]] | frozenset[tuple[str, str]]) -> list[list[str]]:
        return [list(e) for e in sorted(es)]

    return {
        "file": f"examples/cases/{case}.py",
        "expected": edges(blocks.get("EXPECTED", set())),
        "allowed_extra": {k: edges(blocks.get(k, set())) for k in ALLOWED_EXTRA},
        "found": edges(found),
    }


# ---------------------------------------------------------------- code refs
SYM = re.compile(r'sym="([^"]+)"')


class RefError(Exception):
    pass


def used_refs() -> dict[str, list[str]]:
    """sym -> pages that use it, from every docs page and script (generated/ excluded)."""
    out: dict[str, list[str]] = {}
    for p in sorted(DOCS.rglob("*")):
        if p.suffix in (".html", ".js") and OUT.parent not in p.parents:
            for sym in SYM.findall(p.read_text(encoding="utf-8")):
                out.setdefault(sym, []).append(str(p.relative_to(ROOT)))
    return out


def _member(body: list[ast.stmt], name: str) -> ast.stmt | None:
    for s in body:
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and s.name == name:
            return s
        if isinstance(s, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name
                                             for t in s.targets):
            return s
        if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name) and s.target.id == name:
            return s
    return None


def resolve(sym: str) -> JSON:
    """`path::A.b` -> where A.b is defined in path, with its source; `path` alone -> the file."""
    path, _, qual = sym.partition("::")
    file = ROOT / path
    if not file.is_file():
        raise RefError(f"no file {path}")
    if not qual:
        return {"file": path, "line": 1, "end": None, "code": None, "truncated": False}
    text = file.read_text(encoding="utf-8")
    body = ast.parse(text).body
    node: ast.stmt | None = None
    for part in qual.split("."):
        node = _member(body, part)
        if node is None:
            raise RefError(f"no {part!r} in {path}" + (f" (looking for {qual})" if "." in qual else ""))
        body = node.body if isinstance(node, ast.ClassDef) else []
    assert node is not None
    decos = getattr(node, "decorator_list", [])
    start = min([node.lineno, *(d.lineno for d in decos)])
    end = node.end_lineno or node.lineno
    lines = text.splitlines()[start - 1:end]
    return {"file": path, "line": start, "end": end,
            "code": "\n".join(lines[:MAX_SNIPPET]), "truncated": len(lines) > MAX_SNIPPET}


def refs_json() -> JSON:
    out: JSON = {}
    errors: list[str] = []
    for sym, pages in sorted(used_refs().items()):
        try:
            out[sym] = resolve(sym)
        except (RefError, SyntaxError) as e:
            errors.append(f"  {sym}: {e}  (used in {', '.join(sorted(set(pages)))})")
    if errors:
        raise RefError("broken code-refs:\n" + "\n".join(errors))
    return out


# ---------------------------------------------------------------- output
def render() -> str:
    data: JSON = {
        "repo": REPO,
        "refs": refs_json(),
        "examples": {k: example_json(ex) for k, ex in EXAMPLES.items()},
        "cases": {c: case_json(c) for c in CASES},
        "sources": {str(p.relative_to(ROOT)): p.read_text(encoding="utf-8")
                    for p in sorted((ROOT / "callme").rglob("*.py"))},
    }
    body = json.dumps(data, indent=1, ensure_ascii=False)
    return ("// Generated by info/build.py from the real analyzer. Do not edit by hand.\n"
            f"window.DOCS_DATA = {body};\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fail if data.js is out of date")
    args = ap.parse_args()
    if os.environ.get("PYTHONHASHSEED") != "0":
        # Node discovery order follows set iteration order, which follows str hashing: pin it
        # so that two builds of the same code are byte-identical.
        env = {**os.environ, "PYTHONHASHSEED": "0"}
        return subprocess.call([sys.executable, __file__, *sys.argv[1:]], env=env)
    try:
        text = render()
    except RefError as e:
        print(e, file=sys.stderr)
        return 1
    rel = OUT.relative_to(ROOT)
    if args.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print(f"{rel} is out of date: run `python info/build.py`", file=sys.stderr)
            return 1
        print(f"{rel} is up to date")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {rel} ({len(text) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
