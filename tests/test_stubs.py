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


def test_builtins_remaining_stubs_and_fallbacks() -> None:
    src = """
        def leaf(): return 1
        def pair(a, b): return (a(), b())
        def dflt(): return 2
        def f1(): return 3
        def f2(): return 4

        class Ord:
            def __lt__(self, o): return True
            def __repr__(self): return "O"
            def __hash__(self): return 1

        class LenOnly:
            def __len__(self): return 1

        class IdxOnly:
            def __index__(self): return 1

        class StrOnly:
            def __str__(self): return "S"

        class RevIter:
            def __next__(self): return f1

        class HasRevIter:
            def __reversed__(self): return RevIter()

        class HasRevPlain:
            def __reversed__(self): return f2

        class IterOnly:
            def __iter__(self): return RevIter()

        pairs = [(f1, f2)]
        list(map(pair, *pairs))
        o = Ord()
        min(o, o)
        max([], default=dflt)()
        [o].sort()
        repr(o)
        hash(o)
        all([o])
        any([LenOnly()])
        sum(IterOnly())
        int(IdxOnly())
        format(StrOnly(), "")
        format(Ord(), "")
        for fn1 in reversed(HasRevIter()):
            fn1()
        for fn2 in reversed(HasRevPlain()):
            fn2()
        for fn3 in reversed(IterOnly()):
            fn3()
        next(iter([]), dflt)()
        async def run_anext():
            await anext(aiter([]), dflt)()
        run_anext()
        object()
        type(1)
    """
    got = edges(src)
    assert {
        ("<module>", "pair"),
        ("pair", "f1"),
        ("pair", "f2"),
        ("<module>", "Ord.__lt__"),
        ("<module>", "Ord.__repr__"),
        ("<module>", "Ord.__hash__"),
        ("<module>", "dflt"),
        ("<module>", "LenOnly.__len__"),
        ("<module>", "IterOnly.__iter__"),
        ("<module>", "RevIter.__next__"),
        ("<module>", "IdxOnly.__index__"),
        ("<module>", "StrOnly.__str__"),
        ("<module>", "HasRevIter.__reversed__"),
        ("<module>", "HasRevPlain.__reversed__"),
        ("<module>", "f1"),
        ("<module>", "f2"),
        ("run_anext", "dflt"),
    } <= got


def test_functools_partial_widening_fold_and_remaining_stubs() -> None:
    src = """
        import functools

        def a(): return 1
        def b(): return 2
        def c(): return 3
        def d(): return 4
        def kw_impl(x): return x
        def step1(): return step2
        def step2(): return 9

        def call_many(*args, **kwargs):
            for x in args:
                x()
            for y in kwargs.values():
                y()

        # Flattened partial with keyword merging.
        p1 = functools.partial(call_many, a, k=b)
        p2 = functools.partial(p1, c, k=d)
        p2()

        # Widened partial (> MAX_PARTIAL_ARGS) and starred construction.
        pw = functools.partial(call_many, a, b, c, d, a)
        pw()
        pstar = functools.partial(call_many, *(a, b))
        pstar()

        # Recursive partial nesting past MAX_PARTIAL_DEPTH terminates soundly.
        def nest(n, f):
            if n:
                return nest(n - 1, functools.partial(f, a))
            return f
        nest(5, call_many)()

        # Widened partialmethod (> MAX_PARTIAL_ARGS).
        class K:
            def run(self, *args):
                for x in args:
                    x()
            wide = functools.partialmethod(run, a, b, c, d, a)
        K().wide()

        # Bare cache / lru_cache, update_wrapper, total_ordering, singledispatch.dispatch & kw func.
        @functools.cache
        def cached_bare(): return 1
        @functools.lru_cache
        def lru_bare(): return 2
        cached_bare()
        lru_bare()

        def orig(): return 1
        def wrap(): return orig()
        functools.update_wrapper(wrap, orig)()

        @functools.total_ordering
        class Ord:
            def __eq__(self, o): return True
            def __lt__(self, o): return True
        Ord() < Ord()

        @functools.singledispatch
        def sd(x): return a()
        sd.register(str, func=kw_impl)
        sd.dispatch(str)(1)

        # Multi-iteration fold: step 1 returns step2, step 2 calls acc() -> step2.
        def folder(acc, _item):
            return acc()
        functools.reduce(folder, [1, 2], step1)
    """
    got = edges(src)
    assert {
        ("<module>", "call_many"),
        ("call_many", "a"),
        ("call_many", "b"),
        ("call_many", "c"),
        ("call_many", "d"),
        ("<module>", "K.run"),
        ("K.run", "a"),
        ("<module>", "cached_bare"),
        ("<module>", "lru_bare"),
        ("<module>", "wrap"),
        ("wrap", "orig"),
        ("<module>", "Ord.__lt__"),
        ("<module>", "sd"),
        ("<module>", "kw_impl"),
        ("<module>", "folder"),
        ("folder", "step1"),
        ("folder", "step2"),
    } <= got


def test_itertools_full_coverage() -> None:
    src = """
        import itertools

        def acc_fn(x, y): return x()
        def star_fn(x, y): return (x(), y())
        def key_fn(x): return x()
        def pred(x): return True
        def f1(): return 1
        def f2(): return 2
        def f3(): return 3
        def f4(): return 4
        def f5(): return 5
        def f6(): return 6

        class SelIter:
            def __iter__(self): return self
            def __next__(self): return f4

        for _ in itertools.accumulate([f1], func=acc_fn, initial=f2):
            pass
        for _ in itertools.starmap(star_fn, [(f1, f2)]):
            pass
        for _ in itertools.groupby([f3], key=key_fn):
            pass
        for fn in itertools.filterfalse(pred, [f1]):
            fn()
        for fn in itertools.takewhile(pred, [f2]):
            fn()
        for fn in itertools.dropwhile(pred, [f3]):
            fn()
        for fn in itertools.compress([f5], SelIter()):
            fn()
        for fn in itertools.product([f5], [f6]):
            fn()
        for fn in itertools.permutations([f6]):
            fn()
        for fn in itertools.combinations([f6], 1):
            fn()
        for fn in itertools.combinations_with_replacement([f6], 1):
            fn()
        for fn in itertools.pairwise([f6]):
            fn()
        for fn in itertools.batched([f6], 1):
            fn()
        for fn in itertools.tee([f6]):
            fn()
        itertools.count(1)
    """
    got = edges(src)
    assert {
        ("<module>", "acc_fn"),
        ("acc_fn", "f1"),
        ("acc_fn", "f2"),
        ("<module>", "star_fn"),
        ("star_fn", "f1"),
        ("star_fn", "f2"),
        ("<module>", "key_fn"),
        ("key_fn", "f3"),
        ("<module>", "pred"),
        ("<module>", "SelIter.__iter__"),
        ("<module>", "SelIter.__next__"),
        ("<module>", "f5"),
        ("<module>", "f6"),
    } <= got


def test_contextlib_collections_threading_futures_asyncio_remaining() -> None:
    src = """
        import asyncio
        import collections
        import concurrent.futures
        import contextlib
        import threading

        def cb1(): return 1
        def cb2(): return 2
        def cb3(): return 3
        def cb4(): return 4
        def on_sync_push(t, v, tb): return 1
        def on_async_push(t, v, tb): return 1
        def init_fn(arg): return arg()
        def init_arg(): return 1
        def map_fn(x): return x()
        def done_cb(fut): return fut.result()()
        def res_fn(): return 2
        def on_ts(): return 3
        def on_at(): return 4
        def sleep_cb(): return 5
        def await_cb(): return 6

        class Res:
            def __enter__(self): return self
            def __exit__(self, *a): return None
            def close(self): return None

        class ARes:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None

        class CustomAwait:
            def __await__(self): return await_cb

        # contextlib: ExitStack.push(CM & builtin CM), pop_all, close, AsyncExitStack sync/async.
        es = contextlib.ExitStack()
        es.callback(None)
        es.push(None)
        es.push(Res())
        es.push(contextlib.closing(Res()))
        es2 = es.pop_all()
        es2.close()

        aes = contextlib.AsyncExitStack()
        aes.callback(cb1)
        aes.enter_context(Res())
        aes.push(on_sync_push)
        aes.close()
        aes2 = aes.pop_all()
        aes2.push_async_exit(ARes())
        aes2.push_async_exit(on_async_push)
        async def close_aes():
            await aes2.aclose()
        close_aes()
        with contextlib.suppress(ValueError), contextlib.redirect_stdout(None), \
                contextlib.redirect_stderr(None), contextlib.chdir("."):
            pass

        # collections: defaultdict without factory, default_factory(), OrderedDict/Counter/UserDict/UserList, _make.
        dd_plain = collections.defaultdict()
        dd_plain["k"] = cb1
        dd_plain["k"]()
        dd_fac = collections.defaultdict(lambda: cb2)
        dd_fac.default_factory()()
        od = collections.OrderedDict([("a", cb3)])
        od["a"]()
        ul = collections.UserList([cb4])
        ul[0]()
        dq = collections.deque()
        dq.extendleft([cb3])
        dq.popleft()()
        Pair = collections.namedtuple("Pair", ["x"])
        Pair._make([cb4])[0]()

        # threading: run(), Timer with args + cancel(), sync primitives.
        threading.Thread(target=cb1).run()
        tm = threading.Timer(1.0, init_fn, args=(init_arg,))
        tm.start()
        tm.cancel()
        threading.Lock()
        threading.Event()
        threading.local()

        # concurrent.futures: ProcessPoolExecutor + initargs, starred map, set_result before callback, as_completed/wait.
        ex = concurrent.futures.ProcessPoolExecutor(initializer=init_fn, initargs=(init_arg,))
        list(ex.map(map_fn, *([cb2],)))
        ex.shutdown()
        fut = concurrent.futures.Future()
        fut.set_result(res_fn)
        fut.add_done_callback(done_cb)
        fut.done()
        fut.running()
        fut.cancelled()
        fut.cancel()
        fut.exception()
        for f in concurrent.futures.as_completed([fut]):
            f.result()()
        concurrent.futures.wait([fut])

        # asyncio: loop create_future / run_until_complete / call_soon_threadsafe / call_at / lifecycle,
        # wait_for / shield / sleep(result=) / LifoQueue / sync primitives.
        loop = asyncio.new_event_loop()
        loop.call_soon_threadsafe(on_ts)
        loop.call_at(1.0, on_at)
        lf = loop.create_future()
        lf.set_result(res_fn)
        loop.run_until_complete(lf)()
        loop.run_until_complete(CustomAwait())()
        loop.stop()
        loop.run_forever()
        loop.close()

        async def async_rest():
            s = await asyncio.sleep(0, result=sleep_cb)
            s()
            await asyncio.wait_for(asyncio.shield(CustomAwait()), 1.0)
            q = asyncio.LifoQueue()
            q.put_nowait(cb4)
            q.get_nowait()()
            asyncio.Lock()
        asyncio.run(async_rest())
    """
    got = edges(src)
    assert {
        ("<module>", "Res.__enter__"),
        ("<module>", "Res.__exit__"),
        ("<module>", "Res.close"),
        ("<module>", "cb1"),
        ("<module>", "cb2"),
        ("<module>", "cb3"),
        ("<module>", "cb4"),
        ("<module>", "on_sync_push"),
        ("close_aes", "ARes.__aexit__"),
        ("close_aes", "on_async_push"),
        ("<module>", "init_fn"),
        ("init_fn", "init_arg"),
        ("<module>", "map_fn"),
        ("map_fn", "cb2"),
        ("<module>", "done_cb"),
        ("done_cb", "res_fn"),
        ("<module>", "res_fn"),
        ("<module>", "on_ts"),
        ("<module>", "on_at"),
        ("<module>", "CustomAwait.__await__"),
        ("<module>", "await_cb"),
        ("async_rest", "sleep_cb"),
        ("async_rest", "CustomAwait.__await__"),
        ("async_rest", "cb4"),
    } <= got
