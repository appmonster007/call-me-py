"""Tests for builtin and stdlib stubs in callme/stubs/."""
from __future__ import annotations

import json
import textwrap

from callme import analyze_source
from callme.analysis import solve
from callme.frontend import build_program
from callme.output import state_from_dict, state_to_dict


def edges(src: str) -> frozenset[tuple[str, str]]:
    """Return function-level edges of `src` with short names."""
    return analyze_source(textwrap.dedent(src), "t.py", "short").function_edges()


def test_builtins_extensions_and_module_import() -> None:
    src = """
        import builtins
        from builtins import map as bmap

        def gen(): return 1
        def sq(x): return x
        def hit(): return 1

        class C:
            def __bool__(self): return True
            def __abs__(self): return hit()
            def __round__(self): return hit()
            def __int__(self): return 1
            def __float__(self): return 1.0
            def __complex__(self): return 1j
            def __bytes__(self): return b""
            def __format__(self, spec): return ""
            def __aiter__(self): return self
            async def __anext__(self): return hit()

        iter(gen, None)
        c = C()
        any([c])
        abs(c)
        round(c)
        int(c)
        float(c)
        complex(c)
        bytes(c)
        format(c, "")
        builtins.len([c])
        list(bmap(sq, [1]))
        async def run():
            it = aiter(c)
            await anext(it)
        run()
    """
    got = edges(src)
    assert {
        ("<module>", "gen"),
        ("<module>", "sq"),
        ("<module>", "C.__bool__"),
        ("<module>", "C.__abs__"),
        ("<module>", "C.__round__"),
        ("<module>", "C.__int__"),
        ("<module>", "C.__float__"),
        ("<module>", "C.__complex__"),
        ("<module>", "C.__bytes__"),
        ("<module>", "C.__format__"),
        ("run", "C.__aiter__"),
        ("run", "C.__anext__"),
        ("C.__abs__", "hit"),
    } <= got


def test_functools_extensions() -> None:
    src = """
        import functools

        def cb(): return 1
        def cmp(a, b): return 0
        def compute(): return 1
        def default_impl(x): return x
        def int_impl(x): return x
        def str_impl(x): return x
        def direct_impl(x): return x

        @functools.singledispatch
        def show(x):
            return default_impl(x)

        @show.register(int)
        def show_int(x):
            return int_impl(x)

        @show.register
        def show_str(x: str):
            return str_impl(x)

        show.register(float, direct_impl)
        show(1)

        class K:
            @functools.cached_property
            def val(self):
                return compute()

            def run(self, f, x):
                return f()

            run_cb = functools.partialmethod(run, cb)

            @functools.singledispatchmethod
            def handle(self, x):
                return default_impl(x)

            @handle.register(int)
            def _handle_int(self, x):
                return int_impl(x)

        k = K()
        k.val
        k.run_cb(0)
        k.handle(1)
        sorted([1, 2], key=functools.cmp_to_key(cmp))
    """
    got = edges(src)
    assert {
        ("<module>", "show"),
        ("<module>", "show_int"),
        ("<module>", "show_str"),
        ("<module>", "direct_impl"),
        ("<module>", "K.val"),
        ("K.val", "compute"),
        ("<module>", "K.run"),
        ("K.run", "cb"),
        ("<module>", "K.handle"),
        ("<module>", "K._handle_int"),
        ("<module>", "cmp"),
    } <= got


def test_itertools_extensions() -> None:
    src = """
        import itertools

        def a(): return 1
        def b(): return 2
        def c(): return 3
        def d(): return 4

        for f in itertools.chain.from_iterable([[a], [b]]):
            f()
        for f in itertools.repeat(c, 2):
            f()
        for f in itertools.zip_longest([], fillvalue=d):
            f()
    """
    got = edges(src)
    assert {("<module>", "a"), ("<module>", "b"), ("<module>", "c"), ("<module>", "d")} <= got


def test_dataclasses_field_and_helpers() -> None:
    src = """
        from dataclasses import asdict, astuple, dataclass, field, replace

        def make_cb(): return target
        def target(): return 1
        def custom_dict(items): return dict(items)
        def custom_tuple(items): return tuple(items)
        def after(): return 1

        @dataclass
        class C:
            cb: object = field(default_factory=make_cb)

            def __post_init__(self):
                after()

        c = C()
        c.cb()
        asdict(c, dict_factory=custom_dict)
        astuple(c, tuple_factory=custom_tuple)
        replace(c, cb=target)
    """
    got = edges(src)
    assert {
        ("<module>", "make_cb"),
        ("<module>", "target"),
        ("<module>", "C.__post_init__"),
        ("C.__post_init__", "after"),
        ("<module>", "custom_dict"),
        ("<module>", "custom_tuple"),
    } <= got


def test_contextlib_full() -> None:
    src = """
        from contextlib import (
            AsyncExitStack, ExitStack, aclosing, asynccontextmanager,
            closing, contextmanager, nullcontext,
        )

        def yielded_cb(): return 1
        def on_exit(fn): return fn()
        def exit_arg(): return 1
        def on_push(t, v, tb): return 1
        def on_async_cb(): return 1
        def on_null(): return 1

        @contextmanager
        def provide():
            yield yielded_cb

        class Res:
            def __enter__(self): return self
            def __exit__(self, *a): return None
            def close(self): return None
            async def aclose(self): return None

        class ARes:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None

        with provide() as fn:
            fn()

        with closing(Res()):
            pass

        with nullcontext(on_null) as n:
            n()

        with ExitStack() as st:
            st.callback(on_exit, exit_arg)
            st.enter_context(Res())
            st.push(on_push)

        @asynccontextmanager
        async def aprovide():
            yield yielded_cb

        async def run():
            async with aprovide() as afn:
                afn()
            async with aclosing(Res()):
                pass
            async with AsyncExitStack() as ast:
                ast.push_async_callback(on_async_cb)
                await ast.enter_async_context(ARes())

        run()
    """
    got = edges(src)
    assert {
        ("<module>", "provide"),
        ("<module>", "yielded_cb"),
        ("<module>", "Res.close"),
        ("<module>", "on_null"),
        ("<module>", "on_exit"),
        ("on_exit", "exit_arg"),
        ("<module>", "Res.__enter__"),
        ("<module>", "Res.__exit__"),
        ("<module>", "on_push"),
        ("run", "aprovide"),
        ("run", "yielded_cb"),
        ("run", "Res.aclose"),
        ("run", "on_async_cb"),
        ("run", "ARes.__aenter__"),
        ("run", "ARes.__aexit__"),
    } <= got


def test_collections_stubs() -> None:
    src = """
        import collections

        def make_cb(): return created
        def created(): return 1
        def f1(): return 1
        def f2(): return 2
        def f3(): return 3
        def f4(): return 4

        dd = collections.defaultdict(make_cb)
        dd["k"]()

        dq = collections.deque([f1])
        dq.appendleft(f2)
        dq.popleft()()

        cm = collections.ChainMap({"a": f3}, {"b": f4})
        for fn in cm.values():
            fn()

        Pair = collections.namedtuple("Pair", ["x", "y"])
        p = Pair(f1, f2)
        p[0]()
    """
    got = edges(src)
    assert {
        ("<module>", "make_cb"),
        ("<module>", "created"),
        ("<module>", "f1"),
        ("<module>", "f2"),
        ("<module>", "f3"),
        ("<module>", "f4"),
    } <= got


def test_threading_stubs() -> None:
    src = """
        import threading

        def target(x): return x()
        def arg_fn(): return 1
        def on_timer(): return 2
        def on_barrier(): return 3
        def unused(): return 4

        t = threading.Thread(target=target, args=(arg_fn,))
        t.start()
        t.join()

        tm = threading.Timer(1.0, on_timer)
        tm.start()

        b = threading.Barrier(2, action=on_barrier)
        b.wait()

        # Not started: unused must not be called
        threading.Thread(target=unused)
    """
    got = edges(src)
    assert {
        ("<module>", "target"),
        ("target", "arg_fn"),
        ("<module>", "on_timer"),
        ("<module>", "on_barrier"),
    } <= got
    assert ("<module>", "unused") not in got


def test_concurrent_futures_imports_and_callbacks() -> None:
    src = """
        import concurrent.futures
        from concurrent import futures as cf

        def init_worker(): return 1
        def work(x): return x
        def produce(): return yielded
        def yielded(): return 2
        def on_done(fut): return fut.result()()
        def manual_cb(fut): return fut.result()()
        def manual_val(): return 3

        with concurrent.futures.ThreadPoolExecutor(initializer=init_worker) as ex:
            f = ex.submit(produce)
            f.add_done_callback(on_done)
            list(ex.map(work, [1, 2]))

        fut = cf.Future()
        fut.add_done_callback(manual_cb)
        fut.set_result(manual_val)
    """
    got = edges(src)
    assert {
        ("<module>", "init_worker"),
        ("<module>", "produce"),
        ("<module>", "on_done"),
        ("on_done", "yielded"),
        ("<module>", "work"),
        ("<module>", "manual_cb"),
        ("manual_cb", "manual_val"),
    } <= got


def test_asyncio_stubs_and_persistence_roundtrip() -> None:
    src = """
        import asyncio

        def on_soon(cb): return cb()
        def soon_arg(): return 1
        def on_later(): return 2
        def in_thread(x): return x
        def in_exec(): return exec_ret
        def exec_ret(): return 3
        def on_done(t): return t.result()()
        def queued(): return 4

        async def make_fn():
            return exec_ret

        async def main():
            loop = asyncio.get_running_loop()
            loop.call_soon(on_soon, soon_arg)
            loop.call_later(0.1, on_later)
            r = await loop.run_in_executor(None, in_exec)
            r()
            await asyncio.to_thread(in_thread, 1)

            async with asyncio.TaskGroup() as tg:
                t = tg.create_task(make_fn())
                t.add_done_callback(on_done)
            got = await t
            got()

            q = asyncio.Queue()
            await q.put(queued)
            fn = await q.get()
            fn()

        asyncio.run(main())
    """
    prog = build_program(textwrap.dedent(src), "t.py")
    state = solve(prog)
    # Verify round-trip persistence and idempotence with all new Builtin/Box tokens
    back = state_from_dict(prog, json.loads(json.dumps(state_to_dict(state))))
    assert back.same_result(state)
    assert solve(prog, back).same_result(state)

    got = edges(src)
    assert {
        ("<module>", "main"),
        ("main", "on_soon"),
        ("on_soon", "soon_arg"),
        ("main", "on_later"),
        ("main", "in_exec"),
        ("main", "exec_ret"),
        ("main", "in_thread"),
        ("main", "make_fn"),
        ("main", "on_done"),
        ("on_done", "exec_ret"),
        ("main", "queued"),
    } <= got
