"""Scope analysis: the locals, `global` / `nonlocal` declarations and must-writes of a scope.

Everything here reads syntax only. `walk_scope` visits a scope's own nodes without entering
nested functions or classes, and the other functions are built on it.
"""
from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator, Sequence

from .program import FUNC_NODES, SCOPE_NODES, ScopeNode, Sig


def outer_parts(n: ScopeNode) -> list[ast.expr]:
    """Return the parts of a def, lambda or class that run in the enclosing scope.

    These are the decorators, default values, base classes and class keywords: they are
    evaluated where the definition is, not inside it.
    """
    if isinstance(n, ast.ClassDef):
        return [*n.decorator_list, *n.bases, *[k.value for k in n.keywords]]
    a = n.args  # FunctionDef / AsyncFunctionDef / Lambda
    deco = n.decorator_list if not isinstance(n, ast.Lambda) else []
    return [*deco, *a.defaults, *[d for d in a.kw_defaults if d is not None]]


def walk_scope(nodes: Iterable[ast.AST]) -> Iterator[ast.AST]:
    """Yield every AST node that belongs to one scope, in source order.

    Nested defs, lambdas and classes are yielded, together with their parts that run in this
    scope (`outer_parts`), but their bodies are not entered.
    """
    stack: list[ast.AST] = list(reversed(list(nodes)))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, SCOPE_NODES):
            stack.extend(reversed(outer_parts(n)))
        else:
            stack.extend(reversed(list(ast.iter_child_nodes(n))))


def make_sig(args: ast.arguments) -> Sig:
    """Return the `Sig` of a function's argument list."""
    pos = [a.arg for a in args.posonlyargs + args.args]
    defaults: dict[str, ast.expr] = {}
    for name, d in zip(pos[len(pos) - len(args.defaults):], args.defaults):
        defaults[name] = d
    kwonly = [a.arg for a in args.kwonlyargs]
    for name, d in zip(kwonly, args.kw_defaults):
        if d is not None:
            defaults[name] = d
    required = tuple(n for n in pos + kwonly if n not in defaults)
    return Sig(
        tuple(pos), tuple(kwonly),
        args.vararg.arg if args.vararg else None,
        args.kwarg.arg if args.kwarg else None,
        tuple(defaults.items()), required,
    )


def pattern_names(n: ast.AST) -> tuple[str, ...]:
    """Return the names that one `match` pattern node binds itself (not its sub-patterns)."""
    if isinstance(n, (ast.MatchAs, ast.MatchStar)):
        return (n.name,) if n.name else ()
    if isinstance(n, ast.MatchMapping):
        return (n.rest,) if n.rest else ()
    return ()


def scope_locals(body: Iterable[ast.AST], params: Iterable[str]) -> frozenset[str]:
    """Return the names local to one scope: its parameters and every name the scope binds.

    Assignments, deletions, defs, classes, imports, `except ... as` and `match` captures
    count; names declared `global` or `nonlocal` are removed.
    """
    names = set(params)
    declared: set[str] = set()
    for n in walk_scope(body):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, FUNC_NODES + (ast.ClassDef,)):
            names.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                if a.name != "*":
                    names.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            declared.update(n.names)
        names.update(pattern_names(n))
    return frozenset(names - declared)


def scope_decls(body: Iterable[ast.AST]) -> tuple[frozenset[str], frozenset[str]]:
    """Return the names declared `global` and those declared `nonlocal` in one scope."""
    glob: set[str] = set()
    nonloc: set[str] = set()
    for n in walk_scope(body):
        if isinstance(n, ast.Global):
            glob.update(n.names)
        elif isinstance(n, ast.Nonlocal):
            nonloc.update(n.names)
    return frozenset(glob), frozenset(nonloc)


def _target_names(t: ast.expr, tracked: frozenset[str]) -> set[str]:
    """Return names in `tracked` directly bound by assignment target `t`."""
    if isinstance(t, ast.Name):
        return {t.id} if t.id in tracked else set()
    if isinstance(t, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for elt in t.elts:
            out.update(_target_names(elt, tracked))
        return out
    if isinstance(t, ast.Starred):
        return _target_names(t.value, tracked)
    return set()


def _stmt_writes(s: ast.stmt, tracked: frozenset[str]) -> set[str]:
    """Return names in `tracked` unconditionally assigned by simple statement `s`."""
    if isinstance(s, ast.Assign):
        out: set[str] = set()
        for t in s.targets:
            out.update(_target_names(t, tracked))
        return out
    if isinstance(s, ast.AnnAssign):
        return _target_names(s.target, tracked) if s.value is not None else set()
    if isinstance(s, FUNC_NODES + (ast.ClassDef,)):
        return {s.name} if s.name in tracked else set()
    if isinstance(s, (ast.Import, ast.ImportFrom)):
        return {n for a in s.names if a.name != "*"
                and (n := a.asname or a.name.split(".")[0]) in tracked}
    return set()


def _all_deletes(stmts: Sequence[ast.stmt], tracked: frozenset[str]) -> frozenset[str]:
    """Return every name in `tracked` deleted anywhere in `stmts` (within the same scope)."""
    return frozenset(
        t.id for n in walk_scope(stmts) if isinstance(n, ast.Delete)
        for t in n.targets if isinstance(t, ast.Name) and t.id in tracked
    )


def _is_irrefutable(pat: ast.pattern) -> bool:
    """Return True if match pattern `pat` always matches any subject."""
    if isinstance(pat, ast.MatchAs):
        return pat.pattern is None
    if isinstance(pat, ast.MatchOr):
        return any(_is_irrefutable(p) for p in pat.patterns)
    return False


def scope_must_writes(node: ast.AST, body: Sequence[ast.AST],
                      tracked: frozenset[str]) -> frozenset[str]:
    """Return names in `tracked` that `node` definitely assigns on every normal-return path.

    Async functions and generators return empty, because calling them does not execute their
    body immediately. Loops, `with` blocks and `try` handlers are treated conservatively so
    the result is always a sound under-approximation of guaranteed writes.
    """
    if not tracked or not isinstance(node, ast.FunctionDef):
        return frozenset()
    if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in walk_scope(body)):
        return frozenset()
    stmts = [s for s in body if isinstance(s, ast.stmt)]
    returns: list[frozenset[str]] = []
    loop_breaks: list[list[frozenset[str]]] = []

    def intersect(sets: Sequence[frozenset[str]]) -> frozenset[str]:
        """Return the intersection of non-empty sequence `sets`."""
        return sets[0].intersection(*sets[1:])

    def walk(seq: Sequence[ast.stmt], cur: frozenset[str]) -> tuple[frozenset[str], bool]:
        """Return (must-written set at fallthrough, whether fallthrough is reachable)."""
        for s in seq:
            if isinstance(s, ast.Return):
                returns.append(cur)
                return cur, False
            if isinstance(s, ast.Raise):
                return cur, False
            if isinstance(s, ast.Break):
                if loop_breaks:
                    loop_breaks[-1].append(cur)
                return cur, False
            if isinstance(s, ast.Continue):
                return cur, False
            if isinstance(s, ast.If):
                m1, l1 = walk(s.body, cur)
                m2, l2 = walk(s.orelse, cur)
                live = [m for m, ok in ((m1, l1), (m2, l2)) if ok]
                if not live:
                    return cur, False
                cur = intersect(live)
            elif isinstance(s, (ast.For, ast.AsyncFor, ast.While)):
                head = cur - _all_deletes(s.body, tracked)
                loop_breaks.append([])
                walk(s.body, head)
                breaks = loop_breaks.pop()
                m_else, l_else = walk(s.orelse, head)
                exits = ([m_else] if l_else else []) + breaks
                if not exits:
                    return cur, False
                cur = intersect(exits)
            elif isinstance(s, (ast.Try, ast.TryStar)):
                ret_mark = len(returns)
                body_dels = _all_deletes(s.body, tracked)
                h_start = cur - body_dels
                m_body, l_body = walk(s.body, cur)
                if l_body:
                    m_else, l_else = walk(s.orelse, m_body)
                else:
                    m_else, l_else = m_body, False
                ends: list[tuple[frozenset[str], bool]] = [(m_else, l_else)]
                for h in s.handlers:
                    ends.append(walk(h.body, h_start))
                live = [m for m, ok in ends if ok]
                if s.finalbody:
                    fin_dels = _all_deletes(s.finalbody, tracked)
                    if fin_dels:
                        for i in range(ret_mark, len(returns)):
                            returns[i] = returns[i] - fin_dels
                    if live:
                        cur, ok = walk(s.finalbody, intersect(live))
                        if not ok:
                            return cur, False
                    else:
                        walk(s.finalbody, h_start)
                        return cur, False
                else:
                    if not live:
                        return cur, False
                    cur = intersect(live)
            elif isinstance(s, (ast.With, ast.AsyncWith)):
                base = cur - _all_deletes(s.body, tracked)
                m_body, l_body = walk(s.body, cur)
                cur = (base & m_body) if l_body else base
            elif isinstance(s, ast.Match):
                ends = [walk(c.body, cur) for c in s.cases]
                exhaustive = any(c.guard is None and _is_irrefutable(c.pattern) for c in s.cases)
                if not exhaustive:
                    ends.append((cur, True))
                live = [m for m, ok in ends if ok]
                if not live:
                    return cur, False
                cur = intersect(live)
            elif isinstance(s, ast.Delete):
                dels = {t.id for t in s.targets if isinstance(t, ast.Name) and t.id in tracked}
                if dels:
                    cur = cur - dels
            else:
                writes = _stmt_writes(s, tracked)
                if writes:
                    cur = cur | writes
        return cur, True

    end_must, falls_through = walk(stmts, frozenset())
    exits = [*returns, *([end_must] if falls_through else [])]
    return intersect(exits) if exits else frozenset()


def class_known_attrs(body: Sequence[ast.stmt]) -> frozenset[str]:
    """Return the names annotated in a class body or assigned as `self.<name>` in its methods."""
    names: set[str] = set()
    for n in walk_scope(body):
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
        elif isinstance(n, FUNC_NODES):
            pos = [a.arg for a in n.args.posonlyargs + n.args.args]
            if not pos:
                continue
            self_name = pos[0]
            for inner in walk_scope(n.body):
                if (isinstance(inner, ast.Attribute) and isinstance(inner.ctx, ast.Store)
                        and isinstance(inner.value, ast.Name) and inner.value.id == self_name):
                    names.add(inner.attr)
    return frozenset(names)
