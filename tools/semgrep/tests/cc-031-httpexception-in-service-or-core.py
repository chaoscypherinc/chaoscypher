# Fixture for cc-031-httpexception-in-service-or-core.
# raise HTTPException(...) is banned in Core and service layers.

from fastapi import HTTPException


def bad():
    # ruleid: cc-031-httpexception-in-service-or-core
    raise HTTPException(status_code=404, detail="missing")


def ok():
    from chaoscypher_core.exceptions import NotFoundError

    # ok: cc-031-httpexception-in-service-or-core
    raise NotFoundError("missing")
