# Fixture for cc-040-memory-sqlite-in-tests.
# `:memory:` SQLite URLs in tests are banned — use tmp_path / "test.db".


def make_engine():
    from sqlmodel import create_engine

    # ruleid: cc-040-memory-sqlite-in-tests
    return create_engine("sqlite:///:memory:")


def make_engine_alt():
    from sqlmodel import create_engine

    # ruleid: cc-040-memory-sqlite-in-tests
    return create_engine("sqlite://:memory:")


def make_engine_ok(tmp_path):
    from sqlmodel import create_engine

    # ok: cc-040-memory-sqlite-in-tests
    return create_engine(f"sqlite:///{tmp_path}/test.db")
