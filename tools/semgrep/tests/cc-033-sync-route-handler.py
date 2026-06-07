# Fixture for cc-033-sync-route-handler.
# Cortex feature route handlers must be async def.


class _Router:
    def get(self, path, **kw):
        def deco(fn):
            return fn
        return deco


router = _Router()


# ruleid: cc-033-sync-route-handler
@router.get("/items")
def list_items_bad():
    return []


# ok: cc-033-sync-route-handler
@router.get("/items_ok")
async def list_items_ok():
    return []
