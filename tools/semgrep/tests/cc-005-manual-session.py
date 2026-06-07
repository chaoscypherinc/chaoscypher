# Fixture for cc-005-manual-session.
# Bare manual database-session construction outside the session-factory
# files is banned. The rule is a plain regex ('\bSession\s*\(') so any
# textual occurrence of that pattern - including inside comments - will
# fire. Keep this fixture free of the literal pattern in prose.


class Session:  # local stand-in so the fixture parses
    def __init__(self, engine):
        self.engine = engine


def needs_session(engine):
    # ruleid: cc-005-manual-session
    s = Session(engine)
    return s


# ok: cc-005-manual-session
def factory_call():
    from chaoscypher_core.database import get_db_session

    return get_db_session()
