# pyright: basic
"""Compare the Rust frontend with the Python one: the whole `Program` of some entry files.

Both sides print (see `callme-dump program`) the fingerprint and entries, every module, every
function (kind, parent, owner, signature, locals, declarations, must-writes), every class,
and every CFG with its call sites, and the dumps must be identical.

Run it from the directory the entries are relative to, with the Python package importable:

    PYTHONPATH=<python root> python3 callme-rs/tools/dump_program.py [--bin PATH] ENTRY... [-- --path DIR]

Without `--bin` it only prints the Python dump. Each argument may also be a file listing
entries, one per line, prefixed with `@` (e.g. `@entries.txt`); each entry is dumped as its own
program.
"""
from __future__ import annotations

import argparse
import difflib
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

from callme.frontend import Program, call_sites, parse_files


def s(names: Iterable[str]) -> str:
    """Return a sorted, comma-separated list of names."""
    return ",".join(sorted(names))


def dump(prog: Program) -> list[str]:
    """Return the dump of `prog`."""
    out = [f"fingerprint {prog.fingerprint}", f"file {prog.file}", f"module {prog.module.uid}",
           "entries " + " ".join(e.uid for e in prog.entries)]
    for name, m in prog.modules.items():
        out.append(f"M {name} file={m.file} func={m.func.uid if m.func else None} "
                   f"package={m.package} exports={s(m.exports)}")
    for f in prog.funcs.values():
        sig = f.sig
        out.append(f"F {f.id.uid} module={f.id.module} kind={f.kind} "
                   f"parent={f.parent.uid if f.parent else None} owner={f.owner.uid if f.owner else None}")
        out.append(f"  sig pos={','.join(sig.pos)} kwonly={','.join(sig.kwonly)} vararg={sig.vararg} "
                   f"kwarg={sig.kwarg} defaults={','.join(n for n, _ in sig.defaults)} "
                   f"required={','.join(sig.required)}")
        out.append(f"  locals={s(f.locals)} globals={s(f.globals)} nonlocals={s(f.nonlocals)} "
                   f"must={s(f.must_writes)}")
        cfg = prog.cfgs[f.id]
        for n in cfg.nodes:
            site = f" site={n.site.text}|{n.site.kind}" if n.site else ""
            out.append(f"  P {n.idx} {n.kind} {n.line}:{n.col} {n.text!r}{site}")
        for e in cfg.edges:
            out.append(f"  E {e.src}->{e.dst} {e.kind}")
        for site, (c, r) in cfg.calls.items():
            out.append(f"  K {site.line}:{site.col} {site.kind} {site.text!r} {c} {r}")
        for site in call_sites(prog, f.id):
            out.append(f"  S {site.line}:{site.col} {site.kind} {site.text!r}")
    for c in prog.classes.values():
        out.append(f"C {c.id.uid} module={c.id.module} parent={c.parent.uid} "
                   f"known={s(c.known_attrs)}")
    return out


def entries(args: list[str]) -> list[str]:
    """Expand `@file` arguments into the entries they list."""
    out: list[str] = []
    for a in args:
        if a.startswith("@"):
            out.extend(line.strip() for line in Path(a[1:]).read_text().splitlines() if line.strip())
        else:
            out.append(a)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entries", nargs="+")
    ap.add_argument("--bin", help="the callme-dump binary to compare with")
    ap.add_argument("--path", action="append", default=[], help="extra module search directory")
    a = ap.parse_args()
    bad = 0
    todo = entries(a.entries)
    for entry in todo:
        try:
            want = dump(parse_files([entry], search_path=a.path))
        except SyntaxError:
            want = ["E"]
        if not a.bin:
            print("\n".join(want))
            continue
        extra = [x for d in a.path for x in ("--path", d)]
        res = subprocess.run([a.bin, "program", entry, *extra], capture_output=True, text=True)
        got = res.stdout.splitlines()
        if got != want:
            bad += 1
            print(f"DIFF {entry}")
            for line in list(difflib.unified_diff(want, got, "python", "rust", lineterm="", n=1))[:12]:
                print("  " + line)
    if a.bin:
        print(f"{len(todo) - bad}/{len(todo)} programs identical")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
