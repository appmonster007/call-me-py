# pyright: basic
"""Compare the Rust syntax layer with CPython's `ast` on a set of Python files.

For each file both sides dump (see `callme-dump ast`):

  N <type> <line>:<col>   every node in `ast.walk` order, with its position if it has one
  S <line>:<col> <text>   the first line of `ast.unparse` of every statement
  D <line>:<col> <text>   `ast.unparse` of every decorator
  C <line>:<col> <text>   `ast.unparse` of every call

and the dumps must be identical. A file CPython cannot parse must fail in Rust too.

    python3 callme-rs/tools/ast_parity.py [--bin PATH] [--show N] FILE_OR_DIR...

Directories are searched for `*.py`. Exit status 1 if any file differs.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import warnings
from pathlib import Path

SKIP = (ast.expr_context, ast.boolop, ast.operator, ast.unaryop, ast.cmpop)
DEFAULT_BIN = Path(__file__).resolve().parent.parent / "target" / "release" / "callme-dump"


def one_line(s: str) -> str:
    """Escape backslashes and newlines, as `callme-dump` does."""
    return s.replace("\\", "\\\\").replace("\n", "\\n")


def dump(path: Path) -> list[str]:
    """Return CPython's dump of the file at `path`."""
    out = [f"== {path}"]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError, UnicodeDecodeError):
        return out + ["E"]
    nodes = [n for n in ast.walk(tree) if not isinstance(n, SKIP)]
    for n in nodes:
        name = type(n).__name__
        if hasattr(n, "lineno") and not isinstance(n, ast.mod):
            out.append(f"N {name} {getattr(n, 'lineno')}:{getattr(n, 'col_offset')}")
        else:
            out.append(f"N {name}")
    for n in nodes:
        if isinstance(n, ast.stmt):
            first = ast.unparse(n).split("\n", 1)[0]
            out.append(f"S {n.lineno}:{n.col_offset} {one_line(first)}")
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for d in n.decorator_list:
                    out.append(f"D {d.lineno}:{d.col_offset} {one_line(ast.unparse(d))}")
        elif isinstance(n, ast.Call):
            out.append(f"C {n.lineno}:{n.col_offset} {one_line(ast.unparse(n))}")
    return out


def files(args: list[str]) -> list[Path]:
    """Return the Python files named by `args`, directories searched recursively."""
    out: list[Path] = []
    for a in args:
        p = Path(a)
        out.extend(sorted(p.rglob("*.py")) if p.is_dir() else [p])
    return out


def split(lines: list[str]) -> dict[str, list[str]]:
    """Split a multi-file dump into one list of lines per file."""
    out: dict[str, list[str]] = {}
    cur: list[str] = []
    for line in lines:
        if line.startswith("== "):
            cur = out.setdefault(line[3:], [line])
        else:
            cur.append(line)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--bin", default=str(DEFAULT_BIN), help="the callme-dump binary")
    ap.add_argument("--show", type=int, default=3, help="differences to print per file")
    a = ap.parse_args()
    paths = files(a.paths)
    rust: dict[str, list[str]] = {}
    for i in range(0, len(paths), 200):
        chunk = [str(p) for p in paths[i:i + 200]]
        res = subprocess.run([a.bin, "ast", *chunk], capture_output=True, text=True, check=True)
        rust.update(split(res.stdout.splitlines()))
    bad = 0
    for p in paths:
        want = dump(p)
        got = rust.get(str(p), [])
        if want == got:
            continue
        bad += 1
        print(f"DIFF {p}")
        shown = 0
        for i, (w, g) in enumerate(zip(want, got)):
            if w != g:
                print(f"  line {i}: python {w!r}")
                print(f"  {' ' * len(str(i))}       rust   {g!r}")
                shown += 1
                if shown >= a.show:
                    break
        if len(want) != len(got) and shown < a.show:
            print(f"  python has {len(want)} lines, rust {len(got)}")
    print(f"{len(paths) - bad}/{len(paths)} files identical")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
