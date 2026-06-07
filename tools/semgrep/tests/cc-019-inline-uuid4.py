# Fixture for cc-019-inline-uuid4.
# uuid.uuid4() and uuid4() are both banned in production code.

import uuid
from uuid import uuid4


def make_id_bad():
    # ruleid: cc-019-inline-uuid4
    return uuid.uuid4()


def make_id_bad2():
    # ruleid: cc-019-inline-uuid4
    return uuid4()


def make_id_ok():
    from chaoscypher_core.utils.id import generate_id

    # ok: cc-019-inline-uuid4
    return generate_id()
