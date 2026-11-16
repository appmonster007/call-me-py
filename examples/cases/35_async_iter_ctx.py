"""CASE: async with, async for, async generators, async comprehension.
WHY: __aenter__/__aexit__/__anext__ are implicit awaited calls.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> main
  Res.__aenter__ -> setup
  Res.__aexit__ -> teardown
  agen -> step
  main -> AIter.__aiter__
  main -> AIter.__anext__
  main -> Res.__aenter__
  main -> Res.__aexit__
  main -> agen
"""
import asyncio


async def setup():
    return 1


async def teardown():
    return 2


async def step(i):
    return i


class Res:
    async def __aenter__(self):
        return await setup()

    async def __aexit__(self, *a):
        return await teardown()


async def agen():
    for i in range(2):
        yield await step(i)


class AIter:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


async def main():
    async with Res():
        pass
    async for x in agen():
        pass
    async for y in AIter():
        pass
    [z async for z in agen()]


asyncio.run(main())
