# Fixture for cc-026-raw-sql-in-service.
# Raw session.execute / scalar / scalars in feature service.py is banned.


def execute_bad(session):
    # ruleid: cc-026-raw-sql-in-service
    return session.execute("SELECT 1")


def scalar_bad(session):
    # ruleid: cc-026-raw-sql-in-service
    return session.scalar("SELECT 1")


def scalars_bad(session):
    # ruleid: cc-026-raw-sql-in-service
    return session.scalars("SELECT 1")


def ok(repo):
    # ok: cc-026-raw-sql-in-service
    return repo.list_workflows()
