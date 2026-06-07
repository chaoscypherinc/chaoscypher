# Fixture for cc-029-skip-offset-pagination.
# Pagination must use page/page_size, not skip/offset.


class _Router:
    def get(self, path, **kw):
        def deco(fn):
            return fn
        return deco


router = _Router()


# ruleid: cc-029-skip-offset-pagination
@router.get("/items")
async def list_items_bad(skip: int = 0, limit: int = 50):
    return []


# ruleid: cc-029-skip-offset-pagination
@router.get("/things")
async def list_things_bad(offset: int = 0, limit: int = 50):
    return []


# ok: cc-029-skip-offset-pagination
@router.get("/items_ok")
async def list_items_ok(page: int = 1, page_size: int = 50):
    return []
