# Fixture for cc-027-action-verb-in-route.
# Action verbs in route paths (run, execute, trigger, ...) are banned.


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


# ruleid: cc-027-action-verb-in-route
@router.post("/workflows/{id}/run")
async def run_workflow(id: str):
    return {}


# ruleid: cc-027-action-verb-in-route
@router.post("/workflows/{id}/execute")
async def execute_workflow(id: str):
    return {}


# ok: cc-027-action-verb-in-route
@router.post("/workflows/{id}/runs")
async def create_run(id: str):
    return {}


# ok: cc-027-action-verb-in-route — 'executions' is a noun, not a verb
@router.get("/workflows/{id}/executions")
async def list_executions(id: str):
    return []
