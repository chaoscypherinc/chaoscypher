# Fixture for cc-041-asyncio-run-in-tests.
# asyncio.run() inside tests is banned — use @pytest.mark.asyncio.

import asyncio


async def some_coro():
    return 1


def test_bad():
    # ruleid: cc-041-asyncio-run-in-tests
    asyncio.run(some_coro())


async def test_ok():
    # ok: cc-041-asyncio-run-in-tests
    await some_coro()
