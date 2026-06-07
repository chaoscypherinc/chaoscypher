# Fixture for cc-009-sqlmodel-import-in-service.
# Services must not import from sqlmodel directly.

# ruleid: cc-009-sqlmodel-import-in-service
from sqlmodel import select  # noqa: F401, E402

# ruleid: cc-009-sqlmodel-import-in-service
import sqlmodel  # noqa: F401, E402

# ok: cc-009-sqlmodel-import-in-service
from chaoscypher_core.ports import WorkflowRepository  # noqa: F401, E402
