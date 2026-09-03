# Fixture for cc-001-factory-naming.
# The rule flags module-level api.py functions whose name contains
# "service" but doesn't follow get_<feature>_service() / _transport().


# ruleid: cc-001-factory-naming
def make_user_service():
    return None


# ok: cc-001-factory-naming
def get_user_service():
    return None


# ok: cc-001-factory-naming
def get_user_transport():
    return None


class FooService:
    # class methods are not factories
    # ok: cc-001-factory-naming
    def get_service(self):
        return None
