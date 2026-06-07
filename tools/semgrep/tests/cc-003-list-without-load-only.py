# Fixture for cc-003-list-without-load-only.
# list_* methods in repository.py that call select() must also call
# load_only() for explicit column projection.


def select(*args, **kwargs):
    return None


def load_only(*args, **kwargs):
    return None


class Workflow:
    pass


# ruleid: cc-003-list-without-load-only
def list_workflows():
    stmt = select(Workflow)
    return stmt


# ok: cc-003-list-without-load-only
def list_workflows_explicit():
    stmt = select(Workflow).options(load_only(Workflow))
    return stmt
