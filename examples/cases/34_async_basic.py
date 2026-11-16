"""CASE: async def / await chain, create_task, gather, loop.call_soon.
WHY: coroutines are called by awaiting; tasks and callbacks are run by the event loop.
NOTE: tasks, gather and call_soon are run from the loop, so the tracer sees <module> as their caller.
SPAWN (semantic edges a tracer cannot attribute):
  main -> task_fn
  main -> leaf
  main -> mid
  main -> sync_helper
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> leaf
  <module> -> main
  <module> -> mid
  <module> -> sync_helper
  <module> -> task_fn
  main -> mid
  main -> sync_helper
  mid -> leaf
"""
import asyncio


async def leaf():
    return 1


async def mid():
    return await leaf()


async def task_fn():
    return 2


def sync_helper():
    return 3


async def main():
    await mid()
    t = asyncio.create_task(task_fn())
    await t
    await asyncio.gather(leaf(), mid())
    sync_helper()
    loop = asyncio.get_running_loop()
    loop.call_soon(sync_helper)
    await asyncio.sleep(0)


asyncio.run(main())
