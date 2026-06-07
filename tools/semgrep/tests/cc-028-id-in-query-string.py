# Fixture for cc-028-id-in-query-string.
# Resource IDs in the query string (e.g. ?id= or ?source_id=) are banned.


class _Router:
    def get(self, path, **kw):
        def deco(fn):
            return fn
        return deco


router = _Router()


# ruleid: cc-028-id-in-query-string
@router.get("/sources?id=foo")
async def get_source():
    return {}


# ruleid: cc-028-id-in-query-string
@router.get("/sources?source_id=foo")
async def get_source_explicit():
    return {}


# ruleid: cc-028-id-in-query-string
@router.get("/sources?include=foo&id=bar")
async def get_source_second_param():
    return {}


# ok: cc-028-id-in-query-string
@router.get("/sources/{source_id}")
async def get_source_ok(source_id: str):
    return {}


# ok: cc-028-id-in-query-string
@router.get("/sources/{source_id}/chunks")
async def get_chunks_ok(source_id: str):
    return {}
