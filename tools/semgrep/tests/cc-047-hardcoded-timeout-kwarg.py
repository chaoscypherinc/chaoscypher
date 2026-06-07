# Fixture for cc-047-hardcoded-timeout-kwarg.
# Hardcoded timeout=N literals in HTTP / asyncio.wait_for are banned.

import asyncio

import httpx
import requests


async def httpx_async_bad():
    # ruleid: cc-047-hardcoded-timeout-kwarg
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.get("https://example.com")


def requests_bad():
    # ruleid: cc-047-hardcoded-timeout-kwarg
    return requests.get("https://example.com", timeout=10)


async def wait_for_bad():
    async def coro():
        return 1
    # ruleid: cc-047-hardcoded-timeout-kwarg
    return await asyncio.wait_for(coro(), timeout=5)


async def ok_from_settings():
    from chaoscypher_core.app_config import get_settings

    settings = get_settings()
    # ok: cc-047-hardcoded-timeout-kwarg
    async with httpx.AsyncClient(timeout=settings.timeouts.http_seconds) as client:
        return await client.get("https://example.com")
