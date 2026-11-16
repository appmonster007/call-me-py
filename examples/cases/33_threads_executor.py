"""CASE: callbacks run on another thread (Thread target, executor submit/map).
WHY: the spawning function is the semantic caller but is not on the worker's stack.
SPAWN (semantic edges a tracer cannot see):
  <module> -> spawn_target
  <module> -> work
EXPECTED (ground truth from a traced run; caller -> callee):
  spawn_target -> work
"""
import threading
from concurrent.futures import ThreadPoolExecutor


def work(n):
    return n * 2


def spawn_target():
    return work(1)


t = threading.Thread(target=spawn_target)
t.start()
t.join()
with ThreadPoolExecutor(1) as ex:
    ex.submit(work, 3).result()
    list(ex.map(work, [1, 2]))
