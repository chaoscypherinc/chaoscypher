# Fixture for cc-046-hardcoded-sleep-duration.
# Hardcoded sleeps in production code are banned (except asyncio.sleep(0)).

import asyncio
import time


async def bad_async():
    # ruleid: cc-046-hardcoded-sleep-duration
    await asyncio.sleep(5)


def bad_sync():
    # ruleid: cc-046-hardcoded-sleep-duration
    time.sleep(1)


async def ok_yield():
    # ok: cc-046-hardcoded-sleep-duration — sleep(0) is yield-to-loop
    await asyncio.sleep(0)


async def ok_from_settings():
    from chaoscypher_core.app_config import get_settings

    settings = get_settings()
    # ok: cc-046-hardcoded-sleep-duration
    await asyncio.sleep(settings.intervals.poll_seconds)
