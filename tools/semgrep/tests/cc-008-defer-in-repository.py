# Fixture for cc-008-defer-in-repository.
# defer() in repository.py is banned — use load_only() instead.


def defer(*args, **kwargs):
    return None


def load_only(*args, **kwargs):
    return None


class Workflow:
    pass


def get_workflows():
    # ruleid: cc-008-defer-in-repository
    opt = defer(Workflow.large_blob)
    return opt


def get_workflows_ok():
    # ok: cc-008-defer-in-repository
    return load_only(Workflow.id, Workflow.name)
