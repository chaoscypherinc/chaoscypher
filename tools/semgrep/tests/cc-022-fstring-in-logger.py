# Fixture for cc-022-fstring-in-logger.
# Logger event names must be string literals, not f-strings.

import structlog

logger = structlog.get_logger()


def bad():
    name = "x"
    # ruleid: cc-022-fstring-in-logger
    logger.info(f"loaded {name}")


def ok():
    name = "x"
    # ok: cc-022-fstring-in-logger
    logger.info("entity_loaded", name=name)
