# Fixture for cc-006-hyphen-in-api-path.
# Route paths must not contain hyphens.


class _Router:
    def get(self, path, **kw):
        def deco(fn):
            return fn
        return deco

    def post(self, path, **kw):
        def deco(fn):
            return fn
        return deco


router = _Router()


# ruleid: cc-006-hyphen-in-api-path
@router.get("/api/v1/my-resource")
async def list_resources():
    return []


# ruleid: cc-006-hyphen-in-api-path
@router.post("/api/v1/another-thing/{id}")
async def create_thing(id: str):
    return {}


# ok: cc-006-hyphen-in-api-path
@router.get("/api/v1/resources")
async def list_resources_ok():
    return []
