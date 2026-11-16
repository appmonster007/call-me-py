"""CASE: @contextmanager generator, ExitStack callback.
WHY: with-statements call code implicitly (enter/exit) through library wrappers.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> body
  <module> -> managed
  <module> -> release
  managed -> acquire
  managed -> release
"""
from contextlib import contextmanager, ExitStack


def body():
    return 1


def acquire():
    return 1


def release():
    return 2


@contextmanager
def managed():
    acquire()
    try:
        yield
    finally:
        release()


with managed():
    body()
with ExitStack() as st:
    st.callback(release)
