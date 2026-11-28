# pyright: basic
"""Check that the Rust port gives the same results as the Python implementation.

Both CLIs run on the same entry files, in the same working directory, in several modes, and
their outputs are compared after canonicalisation. The Python version orders some lists by
frozenset iteration, which depends on the hash seed, so those lists are compared as sorted
multisets; every other list must match in order:

  JSON      `contexts`, `context_edges` and `icfg.edges` are sorted, without the `#k` suffixes
            of colliding labels (numbered in discovery order too); all else is exact
  DOT       the lines are compared as a sorted multiset
  ICFG DOT  each cluster is keyed by its label (cluster numbers follow discovery order), its
            lines are sorted, and edges between clusters are rewritten with the labels
  wl-json, wl-fi  the Rust worklist solver against the Rust round solver, as JSON
  state     the saved state is decoded to plain values (see `canonical_state`), so the
            numbering of its value table does not matter; it is also loaded across: a state
            saved by Python is resumed by Rust and the other way round, which must give the
            same graph

Usage (from the repository, or with --python-root pointing at a checkout):

    python3 callme-rs/tools/parity.py [--python-root DIR | --ref-commit REV] [--bin PATH]
        [--modes json,dot,icfgdot,module,fi,state] [--snippets FILE] [--jobs N] [ENTRY...]

Without ENTRY arguments every example entry under `<python-root>/examples` is used.
`--snippets` adds the sources recorded by `tools/harvest.py`. Python's outputs are cached in
`--cache` (default: `.parity-cache` under the Rust crate), keyed by the hash of the Python
sources, the examples and the flags, because the Python version is slow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

CRATE = Path(__file__).resolve().parent.parent
MODES: dict[str, list[str]] = {
    "json": ["--icfg"],
    "dot": ["--format", "dot", "--contexts", "--returns"],
    "icfgdot": ["--format", "icfg"],
    "module": ["--id-style", "module"],
    "fi": ["--flow-insensitive", "--include-unreachable"],
    "state": ["--id-style", "module"],  # with --save-state, see run_state
    # the Rust worklist solver against the Rust round solver (no Python involved)
    "wl-json": ["--icfg"],
    "wl-fi": ["--flow-insensitive", "--include-unreachable"],
}
LABEL_SUFFIX = re.compile(r"#\d+$")


# ---------------------------------------------------------------- discovery
def default_entries(root: Path) -> list[str]:
    """Return every example entry of `root`, as paths relative to it."""
    ex = root / "examples"
    out = [str(p.relative_to(root)) for p in sorted(ex.glob("*.py"))]
    if (ex / "project" / "main.py").is_file():
        out.append("examples/project/main.py")
    cases = ex / "cases"
    for p in sorted(cases.rglob("*.py")):
        rel = p.relative_to(cases)
        if len(rel.parts) == 2:  # <family>/<case>.py
            out.append(str(p.relative_to(root)))
        elif len(rel.parts) == 3 and rel.parts[2] == "main.py":  # <family>/<case>/main.py
            out.append(str(p.relative_to(root)))
    return sorted(set(out))


def tree_hash(root: Path) -> str:
    """Return a hash of the Python package and the examples under `root`."""
    h = hashlib.sha256()
    for sub in ("callme", "examples"):
        for p in sorted((root / sub).rglob("*.py")):
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- running
def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run `cmd` in `cwd`; return its exit status and stderr."""
    res = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    return res.returncode, res.stderr


def python_cmd(root: Path) -> tuple[list[str], dict[str, str]]:
    """Return the command and environment that run the Python CLI of `root`."""
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONHASHSEED="0")
    return [sys.executable, "-m", "callme"], env


def outputs(root: Path, rust: str, cache: Path, key: str, entry: str, mode: str) -> tuple[str | None, str | None, str]:
    """Return (python output, rust output, error text) for one entry in one mode."""
    flags = MODES[mode]
    tag = hashlib.sha256(f"{key}|{entry}|{mode}".encode()).hexdigest()[:24]
    py_out = cache / f"{tag}.py.out"
    errors = ""
    with tempfile.TemporaryDirectory() as tmp:
        if mode == "state":
            return run_state(root, rust, cache, tag, entry, Path(tmp))
        if mode.startswith("wl-"):
            ref, out = Path(tmp) / "rounds.out", Path(tmp) / "worklist.out"
            c1, e1 = run([rust, entry, *flags, "-o", str(ref)], root)
            c2, e2 = run([rust, entry, *flags, "--solver", "worklist", "-o", str(out)], root)
            return (ref.read_text() if c1 == 0 else None), (out.read_text() if c2 == 0 else None), (e1 + e2)[-2000:]
        if not py_out.exists():
            cmd, env = python_cmd(root)
            target = Path(tmp) / "py.out"
            code, err = run([*cmd, entry, *flags, "-o", str(target)], root, env)
            if code == 0:
                shutil.copy(target, py_out)
            else:
                py_out.write_text("<error>\n" + err[-2000:])
        rs_target = Path(tmp) / "rs.out"
        code, err = run([rust, entry, *flags, "-o", str(rs_target)], root)
        rs = rs_target.read_text() if code == 0 and rs_target.exists() else None
        if code != 0:
            errors = err[-2000:]
        py = py_out.read_text()
        if py.startswith("<error>"):
            return None, rs, py
        return py, rs, errors


def run_state(root: Path, rust: str, cache: Path, tag: str, entry: str, tmp: Path) -> tuple[str | None, str | None, str]:
    """Save states with both CLIs, compare them, and resume each state with the other CLI.

    The returned "outputs" are canonical JSON texts of (state, graph after cross-loading).
    """
    cmd, env = python_cmd(root)
    flags = MODES["state"]
    py_state = cache / f"{tag}.py.state"
    if not py_state.exists():
        code, err = run([*cmd, entry, *flags, "--save-state", str(tmp / "p.state")], root, env)
        if code != 0:
            return None, None, err[-2000:]
        shutil.copy(tmp / "p.state", py_state)
    code, err = run([rust, entry, *flags, "--save-state", str(tmp / "r.state")], root)
    if code != 0:
        return None, None, err[-2000:]
    # resume each state with the other implementation, then compare the graphs
    code1, err1 = run([rust, entry, *flags, "--load-state", str(py_state), "--icfg", "-o", str(tmp / "r_from_p.json")], root)
    code2, err2 = run([*cmd, entry, *flags, "--load-state", str(tmp / "r.state"), "--icfg", "-o", str(tmp / "p_from_r.json")],
                      root, env)
    if code1 or code2:
        return None, None, (err1 + err2)[-2000:]
    py_doc = json.loads(py_state.read_text())
    rs_doc = json.loads((tmp / "r.state").read_text())
    py = json.dumps({"state": canonical_state(py_doc),
                     "graph": canonical_json(json.loads((tmp / "p_from_r.json").read_text()))}, sort_keys=True)
    rs = json.dumps({"state": canonical_state(rs_doc),
                     "graph": canonical_json(json.loads((tmp / "r_from_p.json").read_text()))}, sort_keys=True)
    return py, rs, ""


# ---------------------------------------------------------------- canonical forms
def dumps(x: Any) -> str:
    """Return a stable text form of a JSON value, used as a sort key."""
    return json.dumps(x, sort_keys=True)


def unsuffix(x: Any) -> Any:
    """Drop the `#k` suffixes of colliding labels; they count nodes in discovery order."""
    if isinstance(x, str):
        return LABEL_SUFFIX.sub("", x)
    if isinstance(x, list):
        return [unsuffix(v) for v in x]
    if isinstance(x, dict):
        return {k: unsuffix(v) for k, v in x.items()}
    return x


def canonical_json(doc: Any) -> Any:
    """Sort the lists of a graph document whose order follows hash-dependent discovery order.

    Those lists also lose the `#k` suffixes of colliding labels, which are numbered in the same
    order; the sorted multiset still checks how many nodes share each label.
    """
    out = dict(doc)
    for key in ("contexts", "context_edges"):
        if isinstance(out.get(key), list):
            out[key] = sorted((unsuffix(v) for v in out[key]), key=dumps)
    icfg = out.get("icfg")
    if isinstance(icfg, dict):
        icfg = dict(icfg)
        icfg["edges"] = sorted((unsuffix(v) for v in icfg["edges"]), key=dumps)
        out["icfg"] = icfg
    return out


def canonical_dot(text: str) -> list[str]:
    """Return the lines of a call graph DOT text, sorted."""
    return sorted(text.splitlines())


def canonical_icfg_dot(text: str) -> Any:
    """Return an ICFG DOT text keyed by cluster labels, with sorted lines."""
    clusters: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    cur: list[str] | None = None
    cur_id = ""
    outside: list[str] = []
    for line in text.splitlines():
        m = re.match(r"\s*subgraph cluster_(\d+) \{", line)
        if m:
            cur, cur_id = [], m.group(1)
            continue
        if cur is not None and line.strip() == "}":
            label = next((x for x in cur if x.strip().startswith("label=")), "")
            names[cur_id] = label.strip()
            clusters.setdefault(label.strip(), []).append("\n".join(sorted(cur)))
            cur = None
            continue
        if cur is not None:
            cur.append(line)
        else:
            outside.append(line)

    def relabel(s: str) -> str:
        """Replace cluster-numbered point ids with the cluster's label."""
        return re.sub(r"n(\d+)_(\d+)", lambda m: f"<{names.get(m.group(1), m.group(1))}>_{m.group(2)}", s)

    return {
        "clusters": {k: sorted(relabel(b) for b in v) for k, v in clusters.items()},
        "outside": sorted(relabel(x) for x in outside),
    }


def canonical_state(doc: Any) -> Any:
    """Return a saved state with every value-table index replaced by the value itself."""
    rows: Any = doc["values"]
    memo: dict[int, Any] = {}

    def val(i: int) -> Any:
        """Return value row `i` as a nested plain value."""
        if i in memo:
            return memo[i]
        r = rows[i]
        kind = r[0]
        if kind == "node":
            v: Any = ["node", r[1], [[n, toks(ids)] for n, ids in r[2]], None if r[3] is None else val(r[3]), r[4]]
        elif kind == "clo":
            v = ["clo", r[1], None if r[2] is None else val(r[2])]  # depth is not part of identity
        elif kind == "builtin":
            v = ["builtin", r[1], [toks(b) for b in r[2]], [[k, toks(b)] for k, b in r[3]]]
        elif kind in ("bound",):
            v = ["bound", val(r[1]), val(r[2])]
        elif kind in ("classmethod", "staticmethod"):
            v = [kind, val(r[1])]
        elif kind == "super":
            v = ["super", r[1], val(r[2])]
        elif kind == "property":
            v = ["property", toks(r[1]), toks(r[2]), toks(r[3])]
        else:
            v = list(r)
        memo[i] = v
        return v

    def toks(ids: list[int]) -> list[Any]:
        """Return a token list as a sorted list of plain values."""
        return sorted((val(i) for i in ids), key=dumps)

    def env(e: dict[str, list[int]]) -> dict[str, Any]:
        """Return an environment with plain values."""
        return {k: toks(v) for k, v in e.items()}

    def by_key(pairs: list[Any]) -> list[Any]:
        """Sort (key, value) rows by their text form."""
        return sorted(pairs, key=dumps)

    d = doc
    return {
        "header": [d["format"], d["version"], d["program"], d["max_ctx"], d["flow_sensitive"]],
        "order": sorted((val(i) for i in d["order"]), key=dumps),
        "envs": by_key([[val(i), env(e)] for i, e in d["envs"]]),
        "ret": by_key([[val(i), toks(t)] for i, t in d["ret"]]),
        "entry": by_key([[val(i), env(e)] for i, e in d["entry"]]),
        "edges": by_key([[val(c), s, val(t)] for c, s, t in d["edges"]]),
        "class_attrs": by_key([[c, env(e)] for c, e in d["class_attrs"]]),
        "inst_attrs": by_key([[val(i), env(e)] for i, e in d["inst_attrs"]]),
        "heap": by_key([[val(i), toks(t)] for i, t in d["heap"]]),
        "extern": by_key([[f, env(e)] for f, e in d["extern"]]),
        "writes": by_key([[val(i), by_key([[f, env(e)] for f, e in ws])] for i, ws in d["writes"]]),
        "bases": by_key([list(x) for x in d["bases"]]),
        "metaclasses": by_key([list(x) for x in d["metaclasses"]]),
        "unknown_decos": by_key([[val(n), s] for n, s in d["unknown_decos"]]),
    }


def canonical(mode: str, text: str) -> Any:
    """Return the canonical form of an output of `mode`."""
    if mode in ("json", "module", "fi", "wl-json", "wl-fi"):
        return canonical_json(json.loads(text))
    if mode == "dot":
        return canonical_dot(text)
    if mode == "icfgdot":
        return canonical_icfg_dot(text)
    return json.loads(text)  # state: already canonical


def first_difference(a: Any, b: Any, path: str = "") -> str:
    """Return a short description of the first place where `a` and `b` differ."""
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict) and isinstance(b, dict):
        for k in list(a) + [k for k in b if k not in a]:
            if k not in a or k not in b:
                return f"{path}/{k}: only in {'python' if k in a else 'rust'}"
            if a[k] != b[k]:
                return first_difference(a[k], b[k], f"{path}/{k}")
        return f"{path}: key order differs"
    if isinstance(a, list) and isinstance(b, list):
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return first_difference(x, y, f"{path}[{i}]")
        return f"{path}: python has {len(a)} items, rust {len(b)}"
    return f"{path}: python {a!r:.300} | rust {b!r:.300}"


# ---------------------------------------------------------------- snippets
def snippet_entries(path: Path, workdir: Path) -> list[str]:
    """Write every harvested source to `workdir` and return the files, relative paths."""
    data = json.loads(path.read_text())
    out: list[str] = []
    seen: set[str] = set()
    for item in data:
        src = item["source"]
        h = hashlib.sha256(src.encode()).hexdigest()[:12]
        if h in seen:
            continue
        seen.add(h)
        f = workdir / f"snip_{h}.py"
        f.write_text(src)
        out.append(f.name)
    return out


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entries", nargs="*")
    ap.add_argument("--python-root", default=str(CRATE.parent), help="checkout holding callme/ and examples/")
    ap.add_argument("--ref-commit", help="compare against this commit of the repository (a temporary checkout)")
    ap.add_argument("--bin", default=str(CRATE / "target" / "release" / "callme"), help="the Rust CLI")
    ap.add_argument("--modes", default="json,dot,icfgdot,module,fi,state")
    ap.add_argument("--snippets", help="JSON file written by tools/harvest.py")
    ap.add_argument("--cache", default=str(CRATE / ".parity-cache"))
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--show", type=int, default=20, help="differences to print")
    a = ap.parse_args()

    root = Path(a.python_root).resolve()
    tmp_root: tempfile.TemporaryDirectory[str] | None = None
    if a.ref_commit:
        tmp_root = tempfile.TemporaryDirectory()
        root = Path(tmp_root.name)
        archive = subprocess.run(["git", "-C", str(CRATE.parent), "archive", a.ref_commit, "callme", "examples"],
                                 capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", str(root)], input=archive.stdout, check=True)
    cache = Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)
    key = tree_hash(root)
    entries = a.entries or default_entries(root)
    snip_dir: tempfile.TemporaryDirectory[str] | None = None
    if a.snippets:
        snip_dir = tempfile.TemporaryDirectory(dir=root)
        rel = Path(snip_dir.name).relative_to(root)
        entries = entries + [str(rel / f) for f in snippet_entries(Path(a.snippets), Path(snip_dir.name))]
    modes = [m for m in a.modes.split(",") if m]
    jobs = [(e, m) for e in entries for m in modes]

    def check(job: tuple[str, str]) -> tuple[str, str, str]:
        """Compare one (entry, mode); return (entry, mode, problem or "")."""
        entry, mode = job
        py, rs, err = outputs(root, str(Path(a.bin).resolve()), cache, key, entry, mode)
        if py is None and rs is None:
            return entry, mode, ""  # both reject the input (e.g. a syntax error): they agree
        if py is None:
            return entry, mode, "" if rs is None else f"python failed, rust did not: {err.strip()[-300:]}"
        if rs is None:
            return entry, mode, f"rust failed: {err.strip()[-300:]}"
        cp, cr = canonical(mode, py), canonical(mode, rs)
        if cp == cr:
            return entry, mode, ""
        return entry, mode, first_difference(cp, cr)

    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        results = list(pool.map(check, jobs))
    bad = [(e, m, p) for e, m, p in results if p]
    for e, m, p in bad[:a.show]:
        print(f"DIFF {m:8} {e}\n    {p}")
    by_mode: dict[str, list[int]] = {}
    for _, m, p in results:
        by_mode.setdefault(m, [0, 0])[0 if not p else 1] += 1
    summary = ", ".join(f"{m} {ok}/{ok + nok}" for m, (ok, nok) in by_mode.items())
    print(f"{len(results) - len(bad)}/{len(results)} identical ({summary})")
    if snip_dir:
        snip_dir.cleanup()
    if tmp_root:
        tmp_root.cleanup()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
