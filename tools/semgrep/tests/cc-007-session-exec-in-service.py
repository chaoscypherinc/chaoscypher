# Fixture for cc-007-session-exec-in-service.
# session.exec() in a feature service.py is forbidden.


class _Service:
    def __init__(self, session):
        self.session = session

    def do_work(self):
        # ruleid: cc-007-session-exec-in-service
        return self.session.exec("SELECT 1")


class _OkService:
    def __init__(self, repo):
        self.repo = repo

    def do_work(self):
        # ok: cc-007-session-exec-in-service
        return self.repo.list_workflows()
