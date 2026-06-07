# Fixture for cc-038-checkconstraint-in-models.
# CheckConstraint in models.py is legacy tech debt — use StrEnum + String.


def CheckConstraint(*args, **kwargs):
    return None


class Workflow:
    # ruleid: cc-038-checkconstraint-in-models
    __table_args__ = (CheckConstraint("status IN ('a','b','c')"),)


# ok: cc-038-checkconstraint-in-models
class WorkflowOk:
    __table_args__ = ()
