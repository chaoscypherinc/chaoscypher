# Fixture for cc-023-logger-bind.
# logger.bind() is banned; use bind_contextvars().

import structlog

logger = structlog.get_logger()


def bad():
    # ruleid: cc-023-logger-bind
    sublog = logger.bind(request_id="abc")
    sublog.info("doing_work")


def ok():
    from structlog.contextvars import bind_contextvars

    # ok: cc-023-logger-bind
    bind_contextvars(request_id="abc")
    logger.info("doing_work")
