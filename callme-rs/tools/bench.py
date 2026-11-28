# pyright: basic
"""Time the Python implementation against the Rust port (both solvers) on some entries.

    python3 callme-rs/tools/bench.py [--python-root DIR] [--bin PATH] [--repeat N]
        [--flags "..."] ENTRY...

Each entry is analysed by `python -m callme`, by the Rust CLI with the round solver, and by
the Rust CLI with `--solver worklist`, with the same flags, and the best wall time of
`--repeat` runs is printed as a Markdown table, with the speedups over Python. Entries are
run from the current directory; process start-up is included in every time.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

CRATE = Path(__file__).resolve().parent.parent


def best(cmd: list[str], env: dict[str, str] | None, repeat: int, timeout: float) -> float | None:
    """Return the best wall time of `repeat` runs of `cmd`, or None if it fails or times out."""
    times: list[float] = []
    for _ in range(repeat):
        t = time.perf_counter()
        try:
            res = subprocess.run(cmd, env=env, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        if res.returncode != 0:
            return None
        times.append(time.perf_counter() - t)
    return min(times)


def fmt(t: float | None) -> str:
    """Return a time for the table."""
    return "timeout/error" if t is None else (f"{t * 1000:.0f} ms" if t < 1 else f"{t:.2f} s")


def ratio(a: float | None, b: float | None) -> str:
    """Return the speedup of `b` over `a`."""
    return "" if a is None or b is None or b == 0 else f"{a / b:.0f}x"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entries", nargs="+")
    ap.add_argument("--python-root", default=str(CRATE.parent))
    ap.add_argument("--bin", default=str(CRATE / "target" / "release" / "callme"))
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--flags", default="", help="extra flags for both CLIs, e.g. \"--flow-insensitive\"")
    ap.add_argument("--timeout", type=float, default=1800)
    a = ap.parse_args()
    flags = a.flags.split()
    env = dict(os.environ, PYTHONPATH=a.python_root, PYTHONHASHSEED="0")
    print(f"flags: `{' '.join(flags) or '(none)'}`\n")
    print("| entry | Python | Rust (rounds) | Rust (worklist) | speedup (rounds) | speedup (worklist) |")
    print("|---|---|---|---|---|---|")
    for e in a.entries:
        out = ["-o", os.devnull]
        py = best([sys.executable, "-m", "callme", e, *flags, *out], env, a.repeat, a.timeout)
        rs = best([a.bin, e, *flags, *out], None, a.repeat, a.timeout)
        wl = best([a.bin, e, *flags, "--solver", "worklist", *out], None, a.repeat, a.timeout)
        print(f"| `{e}` | {fmt(py)} | {fmt(rs)} | {fmt(wl)} | {ratio(py, rs)} | {ratio(py, wl)} |", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
