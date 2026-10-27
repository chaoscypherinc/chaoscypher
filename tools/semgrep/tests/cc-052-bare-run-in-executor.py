# Fixture for cc-052-bare-run-in-executor.
# run_in_executor() without the caller's context is banned in Core.

import asyncio
import contextvars


def work(x):
    return x


async def bad(loop):
    # ruleid: cc-052-bare-run-in-executor
    await loop.run_in_executor(None, work, 1)
    # ruleid: cc-052-bare-run-in-executor
    await asyncio.get_running_loop().run_in_executor(None, lambda: work(2))


async def ok(loop):
    # ok: cc-052-bare-run-in-executor
    await asyncio.to_thread(work, 1)
    # ok: cc-052-bare-run-in-executor
    await loop.run_in_executor(None, contextvars.copy_context().run, work, 1)
    context = contextvars.copy_context()
    # ok: cc-052-bare-run-in-executor
    await loop.run_in_executor(None, context.run, work, 1)
